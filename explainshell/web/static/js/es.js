const debug = false;

const themeCookieName = 'theme';

const log = debug ? console.log.bind(console) : () => {};

// a list of colors to use for the lines
const colors = [
    '#3182bd',
    '#6baed6',
    '#9ecae1',
    '#c6dbef',
    '#e6550d',
    '#fd8d3c',
    '#fdae6b',
    '#fdd0a2',
    '#31a354',
    '#74c476',
    '#a1d99b',
    '#c7e9c0',
    '#756bb1',
    '#9e9ac8',
    '#bcbddc',
    '#dadaeb',
    '#636363',
    '#969696',
    '#bdbdbd',
    '#d9d9d9',
];

// the urls of the themes
let themes = {
    default:
        '//cdnjs.cloudflare.com/ajax/libs/twitter-bootstrap/2.3.1/css/bootstrap.min.css',
    dark: '//maxcdn.bootstrapcdn.com/bootswatch/2.3.1/cyborg/bootstrap.min.css',
};
let hljs_themes = {
    default:
        '//cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/default.min.css',
    dark: '//cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/atom-one-dark.min.css',
};
if (debug) {
    themes = {
        default: '/static/css/bootstrap.min.css',
        dark: '/static/css/bootstrap-cyborg.min.css',
    };
    hljs_themes = {
        default: '/static/css/highlight.default.min.css',
        dark: '/static/css/hljs-atom-one-dark.min.css',
    };
}

function shuffle(arr) {
    const a = arr.slice();
    for (let i = a.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [a[i], a[j]] = [a[j], a[i]];
    }
    return a;
}

function setStyles(els, styles) {
    for (const el of els) Object.assign(el.style, styles);
}

function htmlToElement(html) {
    const template = document.createElement('template');
    template.innerHTML = html.trim();
    return template.content.firstChild;
}

const assignedcolors = {};

let vtimeout;
const changewait = 250;

function specialparam(text) {
    return {
        title: 'Special Parameters',
        content: `<p>The shell treats several parameters specially. These parameters \
may only be referenced; assignment to them is not allowed.</p>${text}`,
    };
}
const expansions = {
    tilde: {
        title: 'Tilde Expansion',
        content: `If a word begins with an unquoted tilde character \
(\u2019<b>~</b>\u2019), all of the characters preceding \
the first unquoted slash (or all characters, if there is no \
unquoted slash) are considered a <i>tilde-prefix</i>. If \
none of the characters in the tilde-prefix are quoted, the \
characters in the tilde-prefix following the tilde are \
treated as a possible <i>login name</i>. If this login name \
is the null string, the tilde is replaced with the value of \
the shell parameter \
<b><small>HOME</small></b><small>.</small> If \
<b><small>HOME</small></b> is unset, the home directory of \
the user executing the shell is substituted instead. \
Otherwise, the tilde-prefix is replaced with the home \
directory associated with the specified login name.`,
    },
    'parameter-param': {
        title: 'Parameter Expansion',
        content: `The \u2019<b>$</b>\u2019 character introduces parameter expansion, command \
substitution, or arithmetic expansion.  The parameter name or \
symbol to be expanded may be enclosed in braces, which are optional \
but serve to protect the variable to be expanded from characters \
immediately following it which could be interpreted as part of the \
name.`,
    },
    'parameter-digits': {
        title: 'Positional Parameters',
        content: `A <i>positional parameter</i> is a parameter denoted by one or more \
digits, other than the single digit 0. Positional parameters are \
assigned from the shell\u2019s arguments when it is invoked, and may be \
reassigned using the <b>set</b> builtin command. Positional \
parameters may not be assigned to with assignment statements. The \
positional parameters are temporarily replaced when a shell \
function is executed (see <b><small>FUNCTIONS</small></b> below). `,
    },
    'parameter-star': specialparam(
        `Expands to the positional parameters, starting from one. When the \
expansion occurs within double quotes, it expands to a single word \
with the value of each parameter separated by the first character \
of the <b><small>IFS</small></b> special variable. That is, \
"<b>$*</b>" is equivalent to \
"<b>$1</b><i>c</i><b>$2</b><i>c</i><b>...</b>", where <i>c</i> is \
the first character of the value of the <b><small>IFS</small></b> \
variable. If <b><small>IFS</small></b> is unset, the parameters are \
separated by spaces. If <b><small>IFS</small></b> is null, the \
parameters are joined without intervening separators.`,
    ),
    'parameter-at': specialparam(
        `Expands to the positional parameters, starting from one. \
When the expansion occurs within double quotes, each \
parameter expands to a separate word. That is, \
"<b>$@</b>" is equivalent to "<b>$1</b>" \
"<b>$2</b>" ... If the double-quoted expansion \
occurs within a word, the expansion of the first parameter \
is joined with the beginning part of the original word, and \
the expansion of the last parameter is joined with the last \
part of the original word. When there are no positional \
parameters, "<b>$@</b>" and <b>$@</b> expand to \
nothing (i.e., they are removed).`,
    ),
    'parameter-pound': specialparam(
        'Expands to the number of positional parameters in',
    ),
    'parameter-question': specialparam(
        `Expands to the exit status of the most recently executed \
foreground pipeline.`,
    ),
    'parameter-hyphen': specialparam(
        `Expands to the current option flags as specified upon invocation, \
by the <b>set</b> builtin command, or those set by the shell \
itself (such as the <b>\u2212i</b> option).`,
    ),
    'parameter-dollar': specialparam(
        `Expands to the process ID of the shell. In a () subshell, it \
expands to the process ID of the current shell, not the \
subshell.`,
    ),
    'parameter-exclamation': specialparam(
        `Expands to the process ID of the most recently executed background \
(asynchronous) command.`,
    ),
    'parameter-zero': specialparam(
        `Expands to the name of the shell or shell script. This is set at \
shell initialization. If <b>bash</b> is invoked with a file of \
commands, <b>$0</b> is set to the name of that file. If <b>bash</b> \
is started with the <b>\u2212c</b> option, then <b>$0</b> is set to the \
first argument after the string to be executed, if one is present. \
Otherwise, it is set to the file name used to invoke <b>bash</b>, \
as given by argument zero.`,
    ),
    'parameter-underscore': specialparam(
        `At shell startup, set to the absolute pathname used to invoke the \
shell or shell script being executed as passed in the environment \
or argument list. Subsequently, expands to the last argument to the \
previous command, after expansion.  Also set to the full pathname \
used to invoke each command executed and placed in the environment \
exported to that command. When checking mail, this parameter holds \
the name of the mail file currently being checked.`,
    ),
};

// a class that represents a group of eslink
class ESLinkGroup {
    constructor(clazz, options, mid) {
        const color = assignedcolors[clazz];
        this.links = options.map(
            (option) => new ESLink(clazz, option, mid, color),
        );
        this.options = this.links.map((l) => l.option);
        this.help = this.links.map((l) => l.help);
    }
}

// this class represents a link (visualized by a line) between a span (option)
// in .command that needs to be connected to a corresponding <pre> in .help
class ESLink {
    constructor(clazz, option, mid, color) {
        this.option = option; // a span from .command
        this.color = color; // the color chosen for this link
        this.paths = []; // a list of d3 paths to draw for this link
        this.lines = []; // a list of d3 lines to draw for this link
        this.circle = null; // circle data to draw, if any (used by unknowns)
        this.text = null; // the text to draw in the circle (always '?')
        this.group = null; // the group this link is a part of

        // unknown links have no corresponding <pre> in .help, they simply show up
        // with a '?' connected to them
        this.unknown = false;

        // unknown links can go either down or up
        this.directiondown = true;

        // clazz is the name of the current group (shell, command0, command1..)
        if (clazz) {
            // the matching <pre> in .help
            this.help = document.getElementById(clazz);

            // each link can go either left or right, we decide where by
            // calculating its middle and comparing it to the middle of .command
            const rr = option.getBoundingClientRect();
            const rrmid = rr.left + rr.width / 2;
            this.goingleft = rrmid <= mid;

            this.help.style.borderColor = this.color;
        }
    }

    leftmost() {
        return this.group.links.find((l) => l.goingleft) ?? null;
    }

    rightmost() {
        return this.group.links.findLast((l) => !l.goingleft) ?? null;
    }

    // return true if this eslink is 'close' to other by looking at their bounding
    // rects
    //
    // we use this when deciding which direction an 'unknown' link should go
    nearby(other) {
        const closeness = 5,
            r = this.option.getBoundingClientRect(),
            rr = other.option.getBoundingClientRect();

        return (
            Math.abs(r.right - rr.left) <= closeness ||
            Math.abs(r.left - rr.right) <= closeness
        );
    }
}

// a convenient wrapper around an array of points that allows to chain appends
class ESPath {
    constructor() {
        this.points = [];
    }

    addpoint(x, y) {
        this.points.push({ x: d3.round(x), y: d3.round(y) });
        return this;
    }
}

// swap the position of two nodes in the DOM
function swapNodes(a, b) {
    const aparent = a.parentNode;
    const asibling = a.nextSibling === b ? a : a.nextSibling;
    b.parentNode.insertBefore(a, b);
    aparent.insertBefore(b, asibling);
}

// reorder the help <pre>'s of all links that go left
function reorder(lefteslinks) {
    const help = lefteslinks.map((l) => l.help),
        visiblehelp = Array.from(
            document.querySelectorAll('#help .help-box'),
        ).filter((el) => el.offsetParent !== null);

    // check the indices of the first and last help boxes. if the first is
    // greater than the last, then it appears later in the DOM which means
    // we've already reordered this set of boxes and they're in the correct
    // order
    if (
        visiblehelp.indexOf(help[0]) >=
        visiblehelp.indexOf(help[help.length - 1])
    )
        return;

    for (
        let i = 0, j = help.length - 1;
        i < Math.floor(help.length / 2) && i !== j;
        i++, j = help.length - 1 - i
    ) {
        const h = help[i],
            hh = help[j];

        swapNodes(h, hh);
    }
}

// return the matching <pre> in .help for each item in commandselector
function helpselector(commandselector) {
    return commandselector.map(function (span) {
        return document.getElementById(this.getAttribute('helpref'));
    });
}

// return the <span>'s in #command that are linked to each <pre> in pres
function optionsselector(pres, spans) {
    const ids = pres.map(function () {
        return this.id;
    });

    const s = $('#command span.unknown');
    const r = Array.from(ids).reduce(
        (s, id) => s.add(`#command span[helpref^=${id}]`),
        s,
    );

    if (typeof spans === 'object') {
        return r.filter(spans);
    } else {
        return r;
    }
}

// initialize the lines logic, deciding which group of elements should be displayed
//
// returns the name of the group (with 'all' meaning draw everything) and two
// selectors: one selects which spans in .command and the other selects their
// matching help text in .help
function initialize() {
    let head = { name: 'all' },
        prev = head,
        groupcount = 0,
        s = $('#command span[class^=shell]'),
        curr;

    if (s.length) {
        const shell = { name: 'shell', commandselector: s, prev: head };
        head.next = shell;
        prev = shell;
        groupcount += 1;
    }

    // construct a doubly linked list of previous/next groups. this is used
    // by the navigation buttons to move between groups
    let i = 0,
        g = `command${i}`;

    s = $(`#command span[class^=${g}]`);

    let unknownsselector = $();

    while (s.length > 0) {
        curr = { name: g, commandselector: s };

        // add this group to the linked list only if it's not full of unknowns
        if (s.filter(':not(.unknown)').length > 0) {
            curr.prev = prev;
            prev.next = curr;
            prev = curr;
            groupcount += 1;
        } else {
            unknownsselector = unknownsselector.add(s);
        }

        i++;
        g = `command${i}`;
        s = $(`#command span[class^=${g}]`);
    }

    if (groupcount === 1) {
        // if we have a single group, get rid of 'all' and remove the prev/next
        // links
        head = head.next;

        delete head.next;
        delete head.prev;

        // if we have 1 group and it's the shell, all other commands in there
        // are unknowns, add them to the selector so they show up as unknowns
        // in the UI
        if (head.name === 'shell') {
            head.commandselector = head.commandselector.add(unknownsselector);
        }
    } else {
        // add a group for all spans at the start
        // select spans that start with command or shell to filter out
        // the dropdown for a command start and expansions in words
        head.commandselector = $('#command span[class^=command]').add(
            '#command span[class^=shell]',
        );

        // fix the prev/next links of the head/tail
        head.prev = prev;
        prev.next = head;
    }

    curr = head;

    // look for expansions in the groups we've created, for each one create
    // a popover with the help text
    while (curr) {
        if (curr.name !== 'all') {
            $('span[class^=expansion]', curr.commandselector).each(function () {
                this.style.color = 'red';
                const kind = this.className.slice(10);

                if (kind in expansions) {
                    log('adding', kind, 'popover to', this);

                    const expansion = expansions[kind];

                    $(this).popover({
                        html: true,
                        placement: 'bottom',
                        trigger: 'hover',
                        title: expansion.title,
                        content: expansion.content,
                    });
                } else {
                    log('kind', kind, 'has no help text!');
                }
            });
        }

        curr = curr.next;
        if (curr === head) break;
    }

    commandunknowns();
    assigncolors();
    handlesynopsis();

    return head;
}

// add the help-synopsis class to all <pre>'s that are
// connected to a .simplecommandstart <span>
function handlesynopsis() {
    helppres.each(function () {
        const spans = optionsselector($(this)).not('.unknown');

        if (spans.is('.simplecommandstart')) {
            this.classList.add('help-synopsis');
        }
    });
}

function assigncolors() {
    // Skip color shuffle when &deterministic is in the URL, so e2e screenshot
    // tests produce pixel-identical SVG lines across runs.
    const params = new URLSearchParams(window.location.search);
    const shuffledcolors = params.has('deterministic')
        ? colors.slice()
        : shuffle(colors);

    document.querySelectorAll('#help .help-box').forEach((el) => {
        const color = shuffledcolors.shift();
        shuffledcolors.push(color);

        assignedcolors[el.id] = color;
    });

    assignedcolors[null] = shuffledcolors.shift();
}

// handle unknowns in #command
function commandunknowns() {
    document.querySelectorAll('#command span.unknown').forEach((el) => {
        // add tooltips
        if (el.classList.contains('simplecommandstart')) {
            el.title = 'This man page seems to be missing...';

            // only add the link to the missing man page issue if we haven't
            // had any expansions in this group (since those might already contain
            // links)
            // if (!el.classList.contains('hasexpansion')) {
            //     const link = document.createElement('a');
            //     link.textContent = el.textContent;
            //     link.href = 'https://github.com/idank/explainshell/issues/1';
            //     el.replaceChildren(link);
            // }
        } else el.title = 'No matching help text found for this argument';
    });
}

function affixon() {
    return document
        .getElementById('command-wrapper')
        .classList.contains('affix');
}

// this is where the magic happens!  we create a connecting line between each
// <span> in the commandselector and its matching <pre> in helpselector.
// for <span>'s that have no help text (such as unrecognized arguments), we attach
// a small '?' to them and refer to them as unknowns
function drawgrouplines(commandselector, options) {
    if (prevselector !== null) {
        clear();
    }

    const defaults = {
        topheight: -1,
        hidepres: true,
    };

    if (typeof options === 'object') {
        options = Object.assign(defaults, options);
    } else {
        options = defaults;
    }

    // define a couple of parameters that control the spacing/padding
    // of various areas in the links
    const sidespace = 20,
        toppadding = 25,
        sidepadding = 15,
        edgedistance = 5,
        unknownlinelength = 15,
        strokewidth = 1;

    const canvas = d3.select('#canvas');
    let canvasTop = document
        .getElementById('canvas')
        .getBoundingClientRect().top;

    // if the current group isn't 'all', hide the rest of the help <pre>'s, and show
    // the <pre>'s help of the current group
    if (options.hidepres) {
        if (currentgroup.name !== 'all') {
            $('#help .help-box')
                .not(helpselector(commandselector))
                .parent()
                .parent()
                .hide();
            helpselector(commandselector).parent().parent().show();
        } else {
            // 'all' group, show everything
            helpselector(commandselector).parent().parent().show();
        }
    }

    if (helpselector(commandselector).length > 0) {
        // the height of the canvas is determined by the bottom of the last visible <pre>
        // in #help
        const visibleHelpBoxes = Array.from(
            document.querySelectorAll('#help .help-box'),
        ).filter((el) => el.offsetParent !== null);
        document.getElementById('canvas').style.height =
            visibleHelpBoxes[
                visibleHelpBoxes.length - 1
            ].getBoundingClientRect().bottom -
            canvasTop +
            'px';

        // need to recompute the top of the canvas after height change
        canvasTop = document
            .getElementById('canvas')
            .getBoundingClientRect().top;
    }

    const commandWrapperRect = document
            .getElementById('command')
            .getBoundingClientRect(),
        mid = commandWrapperRect.left + commandWrapperRect.width / 2;
    let helprect = document.getElementById('help').getBoundingClientRect();

    // the bounds of the area we plan to draw lines in .help
    const top = helprect.top - toppadding,
        left = helprect.left - sidepadding,
        right = helprect.right + sidepadding;
    let topheight = options.topheight;

    if (topheight === -1) {
        topheight = Math.abs(commandWrapperRect.bottom - top);
    }

    // select all spans in our commandselector, and group them by their class
    // attribute. different spans share the same class when they should be
    // linked to the same <pre> in .help
    const groupedoptions = Object.groupBy(
        Array.from(commandselector.filter(':not(.unknown)')),
        (span) => span.getAttribute('helpref'),
    );

    // create an eslinkgroup for every group of <span>'s, these will be linked together to
    // the same <pre> in .help.
    const linkgroups = Object.entries(groupedoptions).map(([clazz, spans]) => {
        const esg = new ESLinkGroup(clazz, spans, mid);
        esg.links.forEach((l) => {
            l.group = esg;
        });

        return esg;
    });

    // an array of all the links we need to make, ungrouped
    let links = linkgroups.flatMap((g) => g.links);
    // the upper bounds of our drawing area
    const marginBetweenCommandAndCanvas = commandWrapperRect.bottom - canvasTop,
        startytop = commandWrapperRect.top - canvasTop;

    // links that are going left and right, in the order we'd like to process
    // them. we reverse right going links so we handle them right-to-left.
    let l = links.filter((l) => l.goingleft),
        r = links.filter((l) => !l.goingleft).reverse();

    // cheat a little: if all our links happen to go left, take half of them
    // to the right (this can happen if the last link happens to strech from
    // before the cutting point all the way to the end)
    if (r.length === 0) {
        const midarr = d3.round(l.length / 2);
        r = l.slice(midarr).reverse();
        l = l.slice(0, midarr);
        r.forEach((l) => {
            l.goingleft = false;
        });
    }

    // we keep track of how many have gone right/left to calculate
    // the spacing distance between the lines
    let goingleft = 0,
        goingright = 0,
        goneleft = 0,
        goneright = 0;

    linkgroups.forEach((esg) => {
        // multiple links in a group count as one in the goingleft/right
        if (esg.links.some((l) => l.goingleft)) goingleft++;

        if (esg.links.some((l) => !l.goingleft)) goingright++;
    });

    links = l.concat(r);

    // handle all links that are not unknowns (have a <pre> in .help)
    //
    // this is hard to explain without an accompanying drawing (TODO)
    for (let i = 0; i < links.length; i++) {
        const link = links[i];

        log('handling', link.option.textContent);
        const commandRect = link.option.getBoundingClientRect(),
            spanmid = commandRect.left + commandRect.width / 2,
            commandRight = commandRect.right - strokewidth;

        link.starty = commandRect.bottom - commandWrapperRect.top + 1;
        const commandOffsetToCanvas =
            marginBetweenCommandAndCanvas - link.starty;

        // points for marker under command
        link.paths.push(
            new ESPath()
                .addpoint(commandRect.left, 0)
                .addpoint(commandRect.left, 5)
                .addpoint(commandRight, 5)
                .addpoint(commandRight, 0),
        );

        const path = new ESPath();
        path.addpoint(spanmid, 5); // 3

        let topskip, y, p, pp;

        if (link.goingleft) {
            const leftmost = link.leftmost();

            // check if this is the leftmost link of the current group; for
            // those we add a line from the option to the help box. the rest of
            // the left going links in this group will connect to the top of
            // this line
            if (link === leftmost) {
                topskip = topheight / goingleft;
                y = topskip * goneleft + topskip + commandOffsetToCanvas;
                path.addpoint(left - (goingleft - goneleft) * sidespace, y); // 4
                helprect = link.help.getBoundingClientRect();
                y =
                    helprect.top -
                    commandWrapperRect.bottom +
                    helprect.height / 2 +
                    commandOffsetToCanvas;
                path.addpoint(left, y);

                link.circle = { x: left + 3, y: y, r: 4 };

                goneleft++;
            } else {
                const leftmostpath = leftmost.paths[leftmost.paths.length - 1];

                p = leftmostpath.points[0];
                pp = leftmostpath.points[1];

                // zero or minus to alight with the same flag
                const startyDifference = leftmost.starty - link.starty;
                path.addpoint(p.x, pp.y + startyDifference);
            }
        } else {
            // handle right going links, similarly to left
            const rightmost = link.rightmost();

            if (link === rightmost) {
                topskip = topheight / goingright;
                y = topskip * goneright + topskip + commandOffsetToCanvas;
                path.addpoint(right + (goingright - goneright) * sidespace, y); // 4
                helprect = link.help.getBoundingClientRect();
                y =
                    helprect.top -
                    commandWrapperRect.bottom +
                    helprect.height / 2 +
                    commandOffsetToCanvas;
                path.addpoint(right, y);

                link.circle = { x: right - 3, y: y, r: 4 };

                goneright++;
            } else {
                const rightmostpath =
                    rightmost.paths[rightmost.paths.length - 1];

                p = rightmostpath.points[0];
                pp = rightmostpath.points[1];

                // zero or minus to alight with the same flag
                const startyDifference = rightmost.starty - link.starty;
                path.addpoint(p.x, pp.y + startyDifference);
            }
        }

        link.paths.push(path);
    }

    // create a group for all the unknowns
    const unknowngroup = new ESLinkGroup(
        null,
        commandselector.filter('.unknown').toArray(),
        mid,
    );
    const linkslengthnounknown = links.length;

    unknowngroup.links.forEach((link, i) => {
        const rr = link.option.getBoundingClientRect(),
            rrright = rr.right - strokewidth,
            nextspan = link.option.nextElementSibling,
            nextlink = links.find((l) => l.option === nextspan),
            prevspan = link.option.previousElementSibling,
            prevlink = links.find((l) => l.option === prevspan);

        link.starty =
            link.option.getBoundingClientRect().bottom -
            link.option.parentElement.getBoundingClientRect().top +
            1;
        const commandOffsetToCanvas =
            marginBetweenCommandAndCanvas - link.starty;
        link.unknown = true;
        link.text = '?';

        link.paths.push(
            new ESPath()
                .addpoint(rr.left, 0)
                .addpoint(rr.left, 5)
                .addpoint(rrright, 5)
                .addpoint(rrright, 0),
        );
        const rrmid = d3.round(rr.left + rr.width / 2);
        link.paths.push(
            new ESPath()
                .addpoint(rrmid, 5 + strokewidth)
                .addpoint(
                    rrmid,
                    5 + strokewidth + unknownlinelength + commandOffsetToCanvas,
                ),
        );
        link.circle = {
            x: rrmid,
            y: 5 + unknownlinelength + 3 + commandOffsetToCanvas,
            r: 8,
        };

        links.push(link);
    });

    if (unknowngroup.links.length > 0) linkgroups.push(unknowngroup);

    // d3 magic starts here
    const fline = d3.svg
        .line()
        .x((d) => d.x)
        .y((d) => d.y)
        .interpolate('step-before');

    // create an svg <g> for every linkgroup
    const groups = canvas.selectAll('g').data(linkgroups).enter().append('g');

    // create an svg <g> for every link
    const moregroups = groups
        .selectAll('g')
        .data((esg) => esg.links)
        .enter()
        .append('g');

    // actually draw the lines we defined above
    moregroups.each(function (link) {
        const g = d3.select(this);

        if (link.directiondown)
            g.attr('transform', `translate(0, ${link.starty})`);

        const paths = g
            .selectAll('path')
            .data(link.paths)
            .enter()
            .append('path')
            .attr('d', (path) => fline(path.points))
            .attr('stroke', link.color)
            .attr('stroke-width', strokewidth)
            .attr('fill', 'none');

        const lines = g
            .selectAll('line')
            .data(link.lines)
            .enter()
            .append('line')
            .attr('x1', (line) => line.x1)
            .attr('y1', (line) => line.y1)
            .attr('x2', (line) => line.x2)
            .attr('y2', (line) => line.y2)
            .attr('stroke', link.color)
            .attr('stroke-width', strokewidth)
            .attr('fill', 'none');

        if (link.circle) {
            const gg = g
                .append('g')
                .attr(
                    'transform',
                    `translate(${link.circle.x}, ${link.circle.y})`,
                );

            gg.append('circle')
                .attr('r', link.circle.r)
                .attr('fill', link.color);

            if (link.text) {
                gg.append('text')
                    .attr('fill', 'white')
                    .attr('text-anchor', 'middle')
                    .attr('y', '.35em')
                    .attr('font-family', 'Arial')
                    .text(link.text);
            }
        }
    });

    // add hover effects for the linkgroups, if we have at least one line that
    // isn't unknown
    if (linkslengthnounknown > 1) {
        const groupsnounknowns = groups.filter((g) => !g.links[0].unknown);
        groupsnounknowns.each((linkgroup) => {
            const othergroups = groups.filter((other) => linkgroup !== other);

            const s = $(linkgroup.help).add(linkgroup.options);
            log('s=', s);
            s.hover(
                () => {
                    /*
                     * we're highlighting a new block,
                     * disable timeout to make all blocks visible
                     **/
                    log(
                        'entering link group =',
                        linkgroup,
                        'clearTimeout =',
                        vtimeout,
                    );
                    clearTimeout(vtimeout);

                    // highlight all the <span>'s of the current group
                    setStyles(linkgroup.options, { fontWeight: 'bold' });

                    // and disable highlighting for substitutions that might
                    // be in there
                    const subs = linkgroup.options.flatMap((el) =>
                        Array.from(
                            el.querySelectorAll('span[class$=substitution]'),
                        ),
                    );
                    setStyles(subs, { fontWeight: 'normal' });
                    // and disable transparency
                    setStyles([...linkgroup.help, ...linkgroup.options], {
                        opacity: '1',
                    });

                    // hide the lines of all other groups
                    groups.attr('visibility', (other) =>
                        linkgroup !== other ? 'hidden' : null,
                    );

                    // and make their <span> and <pre>'s slightly transparent
                    othergroups.each((other) => {
                        setStyles([...other.help, ...other.options], {
                            opacity: '0.4',
                            fontWeight: 'normal',
                        });
                    });
                },
                () => {
                    /*
                     * we're leaving a block,
                     * make all blocks visible unless we enter a
                     * new block within changewait ms
                     **/
                    vtimeout = setTimeout(() => {
                        setStyles(linkgroup.options, { fontWeight: 'normal' });

                        groups.attr('visibility', (other) =>
                            linkgroup !== other ? 'visible' : null,
                        );

                        othergroups.each((other) => {
                            setStyles([...other.help, ...other.options], {
                                opacity: '1',
                            });
                        });
                    }, changewait);

                    log(
                        'leaving link group =',
                        linkgroup,
                        'setTimeout =',
                        vtimeout,
                    );
                },
            );
        });
    }

    prevselector = commandselector;
}

let prevselector = null;

// clear the canvas of all lines and unbind any hover events
// previously set for oldgroup
function clear() {
    document.getElementById('canvas').innerHTML = '';

    if (prevselector) {
        prevselector
            .add(helpselector(prevselector))
            .unbind('mouseenter mouseleave');
        prevselector = null;
    }
}

// very simple adjustment of the command div font size so it doesn't overflow
function adjustcommandfontsize() {
    const commandlength = Array.from(
        document.querySelectorAll(
            '#command span[class^=command], #command span[class^=shell]',
        ),
    )
        .reduce((acc, el) => acc + el.textContent, '')
        .trim().length;
    let commandfontsize;

    if (commandlength > 105) commandfontsize = '10px';
    else if (commandlength > 95) commandfontsize = '12px';
    else if (commandlength > 70) commandfontsize = '14px';
    else if (commandlength > 60) commandfontsize = '16px';

    if (commandfontsize) {
        log(
            'command length',
            commandlength,
            ', adjusting font size to',
            commandfontsize,
        );
        document.getElementById('command').style.fontSize = commandfontsize;
    }
}

let ignorekeydown = false;

function navigation() {
    // if we have more groups, show the prev/next buttons
    if (currentgroup.next) {
        const prev = htmlToElement(
            '<li><i class="icon-arrow-left icon-2"></i><span></span></li>',
        );
        const next = htmlToElement(
            '<li><i class="icon-arrow-right icon-2"></i><span></span></li>',
        );
        const prevnext = htmlToElement(
            '<ul class="inline" id="prevnext"><li>showing <u>all</u>, navigate:</li></ul>',
        );
        prevnext.appendChild(prev);
        prevnext.appendChild(next);
        const navigate = document.getElementById('navigate');
        navigate.style.height = 'auto';
        navigate.appendChild(prevnext);

        const nextext = next.querySelector('span'),
            prevtext = prev.querySelector('span'),
            currentext = prevnext.querySelector('u');

        const grouptext = (group) => {
            if (group.name === 'shell') return 'shell syntax';
            else if (group.name !== 'all')
                return group.commandselector[0].textContent;
            return 'all';
        };

        nextext.textContent = ` explain ${grouptext(currentgroup.next)}`;
        prevtext.textContent = ` explain ${grouptext(currentgroup.prev)}`;

        prev.addEventListener('click', () => {
            if (affixon()) return;
            if (currentgroup.prev) {
                log(
                    'moving to the previous group (%s), current group is %s',
                    currentgroup.prev.name,
                    currentgroup.name,
                );
                const oldgroup = currentgroup;
                currentgroup = currentgroup.prev;
                currentext.textContent = grouptext(currentgroup);

                // no need to potentically call drawvisible() here since for now
                // we don't allow clicking when scrolling
                drawgrouplines(currentgroup.commandselector);

                if (!currentgroup.prev) {
                    log(
                        'new current group is the first group, disabling prev button',
                    );
                    prev.style.display = 'none';
                    prevtext.textContent = '';
                } else {
                    log(
                        'setting prev button text to new current group prev %s',
                        currentgroup.prev.name,
                    );

                    prevtext.textContent = ` explain ${grouptext(currentgroup.prev)}`;
                }

                if (currentgroup.next) {
                    next.style.display = '';
                    nextext.textContent = ` explain ${grouptext(currentgroup.next)}`;
                    log(
                        'setting next button text to new current group next %s',
                        currentgroup.next.name,
                    );
                }
            }
        });

        next.addEventListener('click', () => {
            if (affixon()) return;
            if (currentgroup.next) {
                log(
                    'moving to the next group (%s), current group is %s',
                    currentgroup.next.name,
                    currentgroup.name,
                );
                const oldgroup = currentgroup;
                currentgroup = currentgroup.next;
                currentext.textContent = grouptext(currentgroup);

                // no need to potentically call drawvisible() here since for now
                // we don't allow clicking when scrolling
                drawgrouplines(currentgroup.commandselector);

                if (!currentgroup.next) {
                    log(
                        'new current group is the last group, disabling next button',
                    );
                    next.style.display = 'none';
                    nextext.textContent = '';
                } else {
                    log(
                        'setting next button text to new current group next %s',
                        currentgroup.next.name,
                    );
                    nextext.textContent = ` explain ${grouptext(currentgroup.next)}`;
                }

                if (currentgroup.prev) {
                    prev.style.display = '';
                    prevtext.textContent = ` explain ${grouptext(currentgroup.prev)}`;
                    log(
                        'setting prev button text to new current group prev %s',
                        currentgroup.prev.name,
                    );
                }
            }
        });

        // disable key navigation when the user focuses on the search box
        document.getElementById('top-search').addEventListener('focus', () => {
            ignorekeydown = true;
        });

        document.getElementById('top-search').addEventListener('blur', () => {
            ignorekeydown = false;
        });

        // bind left/right arrows as well
        document.addEventListener('keydown', (e) => {
            if (!ignorekeydown) {
                switch (e.which) {
                    case 37: // left
                        prev.click();
                        break;
                    case 39: // right
                        next.click();
                        break;
                    default:
                        return;
                }

                e.preventDefault();
            }
        });
    }
}

function inview(viewtop, viewbottom, el) {
    const rect = el.getBoundingClientRect();
    const elemmiddle = rect.top + window.scrollY + rect.height / 2;

    // we consider the element to be in view when its middle is
    // within the viewport
    return viewtop < elemmiddle && viewbottom > elemmiddle;
}

function drawvisible() {
    let viewtop = window.scrollY;
    const viewbottom = viewtop + window.innerHeight,
        topspace = 80;

    viewtop += topspace;

    const visible = Array.from(
        document.querySelectorAll('#help .help-box'),
    ).filter(
        (el) => el.offsetParent !== null && inview(viewtop, viewbottom, el),
    );

    if (visible.length > 0) {
        const commandselector = optionsselector(
            $(visible),
            currentgroup.commandselector,
        );
        drawgrouplines(commandselector, { topheight: 50, hidepres: false });
    } else {
        clear();
    }
}

function draw() {
    if (affixon()) drawvisible();
    else {
        drawgrouplines(currentgroup.commandselector);
    }
}

function setTheme(theme) {
    log('setting theme to', theme);

    document.getElementById('bootstrapCSS').setAttribute('href', themes[theme]);
    document.getElementById('hljsCSS').setAttribute('href', hljs_themes[theme]);

    document.documentElement.setAttribute('data-theme', theme);
    document.body.dataset.theme = theme;
    localStorage.setItem(themeCookieName, theme);
}

// Theme-related stuff
$(document).ready(() => {
    $('#settingsContainer .dropdown-menu a').click(function () {
        setTheme(this.dataset.themeName);
    });
});
