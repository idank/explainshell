import itertools
import collections
import logging

import nltk
import nltk.metrics
import nltk.classify
import nltk.classify.maxent

from explainshell import algo, config

logger = logging.getLogger(__name__)


def get_features(paragraph):
    features = {}
    p_text = paragraph.cleantext()
    assert p_text

    features["starts_with_hyphen"] = algo.features.starts_with_hyphen(p_text)
    features["is_indented"] = algo.features.is_indented(p_text)
    features["par_length"] = algo.features.par_length(p_text)
    for w in ("=", "--", "[", "|", ","):
        features[f"first_line_contains_{w}"] = algo.features.first_line_contains(
            p_text, w
        )
    features["first_line_length"] = algo.features.first_line_length(p_text)
    features["first_line_word_count"] = algo.features.first_line_word_count(p_text)
    features["is_good_section"] = algo.features.is_good_section(paragraph)
    features["word_count"] = algo.features.word_count(p_text)

    return features


class Classifier:
    """classify the paragraphs of a man page as having command line options
    or not"""

    def __init__(self, store, algo, **classifier_args):
        self.store = store
        self.algo = algo
        self.classifier_args = classifier_args
        self.classifier = None

    def train(self):
        if self.classifier:
            return

        man_pages = self.store.trainingset()

        # flatten the manpages so we get a list of (manpage-name, paragraph)
        def flatten_manpages(manpage):
            p_list = []
            for para in manpage.paragraphs:
                p_list.append(para)
            return p_list

        paragraphs = itertools.chain(*[flatten_manpages(m) for m in man_pages])
        training = list(paragraphs)

        negids = [p for p in training if not p.is_option]
        posids = [p for p in training if p.is_option]

        negfeats = [(get_features(p), False) for p in negids]
        posfeats = [(get_features(p), True) for p in posids]

        negcutoff = len(negfeats) * 3 / 4
        poscutoff = len(posfeats) * 3 / 4

        trainfeats = negfeats[:negcutoff] + posfeats[:poscutoff]
        self.testfeats = negfeats[negcutoff:] + posfeats[poscutoff:]

        logger.info("train on %d instances", len(trainfeats))

        if self.algo == "maxent":
            c = nltk.classify.maxent.MaxentClassifier
        elif self.algo == "bayes":
            c = nltk.classify.NaiveBayesClassifier
        else:
            raise ValueError("unknown classifier")

        self.classifier = c.train(trainfeats, **self.classifier_args)

    def evaluate(self):
        self.train()
        ref_sets = collections.defaultdict(set)
        test_sets = collections.defaultdict(set)

        for i, (feats, label) in enumerate(self.testfeats):
            ref_sets[label].add(i)
            guess = self.classifier.prob_classify(feats)
            observed = guess.max()
            test_sets[observed].add(i)
            # if label != observed:
            #    print('label:', label, 'observed:', observed, feats

        print("pos precision:", nltk.metrics.precision(ref_sets[True], test_sets[True]))
        print("pos recall:", nltk.metrics.recall(ref_sets[True], test_sets[True]))
        print("neg precision:", nltk.metrics.precision(ref_sets[False], test_sets[False]))
        print("neg recall:", nltk.metrics.recall(ref_sets[False], test_sets[False]))

        print(self.classifier.show_most_informative_features(10))

    def classify(self, manpage):
        self.train()
        for item in manpage.paragraphs:

            features = get_features(item)
            guess = self.classifier.prob_classify(features)
            option = guess.max()
            certainty = guess.prob(option)

            if option and certainty >= config.CLASSIFIER_CUTOFF:
                logger.info(
                    "classified %s (%f) as an option paragraph", item, certainty
                )
                item.is_option = True
                yield certainty, item
