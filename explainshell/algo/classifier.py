import itertools, collections, logging

import nltk
import nltk.metrics
import nltk.classify
import nltk.classify.maxent

from explainshell import algo, config

logger = logging.getLogger(__name__)

def get_features(paragraph):
    features = {}
    ptext = paragraph.cleantext()
    assert ptext

    features['starts_with_hyphen'] = algo.features.starts_with_hyphen(ptext)
    features['is_indented'] = algo.features.is_indented(ptext)
    features['par_length'] = algo.features.par_length(ptext)
    for w in ('=', '--', '[', '|', ','):
        features['first_line_contains_%s' % w] = algo.features.first_line_contains(ptext, w)
    features['first_line_length'] = algo.features.first_line_length(ptext)
    features['first_line_word_count'] = algo.features.first_line_word_count(ptext)
    features['is_good_section'] = algo.features.is_good_section(paragraph)
    features['word_count'] = algo.features.word_count(ptext)

    return features

class classifier(object):
    '''classify the paragraphs of a man page as having command line options
    or not'''
    def __init__(self, store, algo, **classifier_args):
        self.store = store
        self.algo = algo
        self.classifier_args = classifier_args
        self.classifier = None

    def train(self):
        if self.classifier:
            return

        manpages = self.store.trainingset()

        # flatten the manpages so we get a list of (manpage-name, paragraph)
        def flatten_manpages(manpage):
            l = []
            for para in manpage.paragraphs:
                l.append(para)
            return l
        paragraphs = itertools.chain(*[flatten_manpages(m) for m in manpages])
        training = list(paragraphs)

        negids = [p for p in training if not p.is_option]
        posids = [p for p in training if p.is_option]

        negfeats = [(get_features(p), False) for p in negids]
        posfeats = [(get_features(p), True) for p in posids]

        negcutoff = len(negfeats)*3//4
        poscutoff = len(posfeats)*3//4

        trainfeats = negfeats[:negcutoff] + posfeats[:poscutoff]
        self.testfeats = negfeats[negcutoff:] + posfeats[poscutoff:]

        logger.info('train on %d instances', len(trainfeats))

        if self.algo == 'maxent':
            c = nltk.classify.maxent.MaxentClassifier
        elif self.algo == 'bayes':
            c = nltk.classify.NaiveBayesClassifier
        else:
            raise ValueError('unknown classifier')

        self.classifier = c.train(trainfeats, **self.classifier_args)

    def evaluate(self):
        self.train()
        refsets = collections.defaultdict(set)
        testsets = collections.defaultdict(set)

        for i, (feats, label) in enumerate(self.testfeats):
            refsets[label].add(i)
            guess = self.classifier.prob_classify(feats)
            observed = guess.max()
            testsets[observed].add(i)
            #if label != observed:
            #    print 'label:', label, 'observed:', observed, feats

        print('pos precision:', nltk.metrics.precision(refsets[True], testsets[True]))
        print('pos recall:', nltk.metrics.recall(refsets[True], testsets[True]))
        print('neg precision:', nltk.metrics.precision(refsets[False], testsets[False]))
        print('neg recall:', nltk.metrics.recall(refsets[False], testsets[False]))

        print(self.classifier.show_most_informative_features(10))

    def classify(self, manpage):
        self.train()
        for item in manpage.paragraphs:

            features = get_features(item)
            guess = self.classifier.prob_classify(features)
            option = guess.max()
            certainty = guess.prob(option)

            if option:
                if certainty < config.CLASSIFIER_CUTOFF:
                    pass
                else:
                    logger.info('classified %s (%f) as an option paragraph', item, certainty)
                    item.is_option = True
                    yield certainty, item
