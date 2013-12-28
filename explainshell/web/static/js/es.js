jQuery.fn.reverse = [].reverse;

var debug = false;

if (!debug) {
    console = console || {};
    console.log = function(){};
}

// a list of colors to use for the lines
var colors = ['#3182bd', '#6baed6', '#9ecae1', '#c6dbef', '#e6550d', '#fd8d3c', '#fdae6b', '#fdd0a2', '#31a354', '#74c476', '#a1d99b', '#c7e9c0', '#756bb1', '#9e9ac8', '#bcbddc', '#dadaeb', '#636363', '#969696', '#bdbdbd', '#d9d9d9'];

var assignedcolors = {};

var vtimeout,
    changewait = 250;

// a class that represents a group of eslink
function eslinkgroup(clazz, options, mid) {
    var color = assignedcolors[clazz];
    this.links = options.map(function(option) { return new eslink(clazz, option, mid, color); });
    this.options = _.pluck(this.links, 'option');
    this.help = _.pluck(this.links, 'help');
}

// this class represents a link (visualized by a line) between a span (option)
// in .command that needs to be connected to a corresponding <pre> in .help
function eslink(clazz, option, mid, color) {
    this.option = option;       // a span from .command
    this.color = color;         // the color chosen for this link
    this.paths = new Array();   // a list of d3 paths to draw for this link
    this.lines = new Array();   // a list of d3 lines to draw for this link
    this.circle = null;         // circle data to draw, if any (used by unknowns)
    this.text = null;           // the text to draw in the circle (always '?')
    this.group = null;          // the group this link is a part of

    // unknown links have no corresponding <pre> in .help, they simply show up
    // with a '?' connected to them
    this.unknown = false;

    // unknown links can go either down or up
    this.directiondown = true;

    // clazz isthe name of the current group (shell, command0, command1..)
    if (clazz) {
        // the matching <pre> in .help
        this.help = $("#" + clazz)[0];

        // each link can go either left or right, we decide where by
        // calculating its middle and comparing it to the middle of .command
        var rr = option.getBoundingClientRect();
        var rrmid = rr.left + rr.width / 2;
        this.goingleft = rrmid <= mid;

        $(this.help).css("border-color", this.color);
        $(this.help).css("background-color", "white");
    }
}

eslink.prototype.leftmost = function() {
    for (var i = 0; i < this.group.links.length; i++) {
        if (this.group.links[i].goingleft)
            return this.group.links[i];
    }

    return null;
}

eslink.prototype.rightmost = function() {
    for (var i = this.group.links.length-1; i >= 0; i--) {
        if (!this.group.links[i].goingleft)
            return this.group.links[i];
    }

    return null;
}

// return true if this eslink is 'close' to other by looking at their bounding
// rects
//
// we use this when deciding which direction an 'unknown' link should go
eslink.prototype.nearby = function(other) {
    var closeness = 5,
        r = this.option.getBoundingClientRect(), rr = other.option.getBoundingClientRect();

    return Math.abs(r.right - rr.left) <= closeness || Math.abs(r.left - rr.right) <= closeness;
}

// a conveninent wrapper around an array of points that allows to chain appends
function espath() {
    this.points = new Array();
}

espath.prototype.addpoint = function(x, y) {
    this.points.push({"x": d3.round(x), "y": d3.round(y)});
    return this;
}

// swap the position of two nodes in the DOM
function swapNodes(a, b) {
    var aparent = a.parentNode;
    var asibling = a.nextSibling === b ? a : a.nextSibling;
    b.parentNode.insertBefore(a, b);
    aparent.insertBefore(b, asibling);
}

// reorder the help <pre>'s of all links that go left
function reorder(lefteslinks) {
    var help = _.pluck(lefteslinks, 'help'),
        visiblehelp = $("#help pre:visible");

    // check the indices of the first and last help boxes. if the first is
    // greater than the last, then it appears later in the DOM which means
    // we've already reordered this set of boxes and they're in the correct
    // order
    if (visiblehelp.index($(help[0])) >= visiblehelp.index($(help[help.length - 1])))
        return;

    for (var i = 0, j = help.length - 1; i < Math.floor(help.length / 2) && i != j; i++, j = help.length - 1 - i) {
        var h = help[i],
            hh = help[j];

        swapNodes(h, hh);
    }
}

// return the matching <pre> in .help for each item in commandselector
function helpselector(commandselector) {
    return commandselector.map(function(span) {
        return $("#" + $(this).attr('helpref'))[0];
    });
}

// return the <span>'s in #command that are linked to each <pre> in pres
function optionsselector(pres) {
    var ids = pres.map(function() {
        return $(this).attr('id');
    });

    var s = $("#command span.unknown");
    var r = _.reduce(ids, function(s, id) { return s.add("#command span[helpref^=" + id + "]"); }, s);

    return (r.filter(currentgroup.commandselector));
}

// initialize the lines logic, deciding which group of elements should be displayed
//
// returns the name of the group (with 'all' meaning draw everything) and two
// selectors: one selects which spans in .command and the other selects their
// matching help text in .help
function initialize() {
    var head = {'name' : 'all'},
        prev = head,
        groupcount = 0,
        s = $("#command span[class^=shell]");

    if (s.length) {
        var shell = {'name' : 'shell', 'commandselector' : s, 'prev' : head};
        head['next'] = shell;
        prev = shell;
        groupcount += 1;
    }

    // construct a doubly linked list of previous/next groups. this is used
    // by the navigation buttons to move between groups
    var i = 0,
        g = "command" + i,
        s = $("#command span[class^=" + g + "]");

    while (s.length > 0) {
        var curr = {'name' : g, 'commandselector' : s}

        if (s.filter(':not(.unknown)').length > 0) {
            curr['prev'] = prev;
            prev['next'] = curr;
            prev = curr;
            groupcount += 1;
        }

        i++;
        g = "command" + i;
        s = $("#command span[class^=" + g + "]")
    }

    if (groupcount == 1) {
        // if we have a single group, get rid of 'all' and remove the prev/next
        // links
        head = head['next'];

        delete head['next'];
        delete head['prev'];
    }
    else {
        // fix the prev/next links of the head/tail
        head['commandselector'] = $("#command span[class]").filter(":not(.dropdown)");
        head['prev'] = prev;
        prev['next'] = head;
    }

    commandunknowns();
    assigncolors();

    return head;
}

function assigncolors() {
    var shuffledcolors = _.shuffle(colors);

    $("#help pre").each(function() {
        assignedcolors[$(this).attr('id')] = shuffledcolors.shift();
    });

    assignedcolors[null] = shuffledcolors.shift();
}

// handle unknowns in #command
function commandunknowns() {
    $("#command span.unknown").each(function(span) {
        $this = $(this);

        // add tooltips
        if ($this.hasClass('simplecommandstart')) {
            this.title = "This man page seems to be missing...";

            // add a github's issue for missing man pages
            // issue and possibly send a link
            var link = $("<a/>").text($this.text())
                                .attr('href', 'https://github.com/idank/explainshell/issues/1');
            $this.html(link);
        }
        else
            this.title = "No matching help text found for this argument";
    });
}

function affixon() {
    return $("#command-wrapper").hasClass("affix");
}

// this is where the magic happens!  we create a connecting line between each
// <span> in the commandselector and its matching <pre> in helpselector.
// for <span>'s that have no help text (such as unrecognized arguments), we attach
// a small '?' to them and refer to them as unknowns
function drawgrouplines(commandselector, options) {
    if (prevselector != null) {
        clear();
    }

    var defaults = {
        topheight: -1,
        hidepres: true
    };

    if (typeof options == 'object') {
        options = $.extend(defaults, options);
    } else {
        options = defaults;
    }

    // define a couple of parameters that control the spacing/padding
    // of various areas in the links
    var sidespace = 20, toppadding = 25, sidepadding = 15, edgedistance = 5,
        unknownlinelength = 15, strokewidth = 1;

    var canvas = d3.select("#canvas"),
        canvastop = $("#canvas")[0].getBoundingClientRect().top;

    // if the current group isn't 'all', hide the rest of the help <pre>'s, and show
    // the <pre>'s help of the current group
    if (options.hidepres) {
        if (currentgroup.name != 'all') {
            $("#help pre").not(helpselector(commandselector)).parent().parent().hide();
            helpselector(commandselector).parent().parent().show();

            // the first item in a non-shell group is always the synopsis of the
            // command (unless it's unknown). we display it at the top without a
            // connecting line, so remove it from the selectors (unless it's the
            // only one)
            //if (currentgroup.name != 'shell' && !$(commandselector[0]).hasClass('unknown') &&
            //    commandselector.filter(':not(.unknown)').length > 1) {
            //    console.log('slicing command selector');
            //    commandselector = commandselector.slice(1);
            //}
        }
        else {
            // 'all' group, show everything
            helpselector(commandselector).parent().parent().show();
        }
    }

    if (helpselector(commandselector).length > 0) {
        // the height of the canvas is determined by the bottom of the last visible <pre>
        // in #help
        $("#canvas").height($("#help pre:visible").last()[0].getBoundingClientRect().bottom - canvastop);

        // need to recompute the top of the canvas after height change
        canvastop = $("#canvas")[0].getBoundingClientRect().top;
    }

    var commandrect = $("#command")[0].getBoundingClientRect(),
        mid = commandrect.left + commandrect.width / 2,
        helprect = $("#help")[0].getBoundingClientRect();

    // the bounds of the area we plan to draw lines in .help
    var top = helprect.top - toppadding,
        left = helprect.left - sidepadding,
        right = helprect.right + sidepadding,
        topheight = options.topheight;

    if (topheight == -1) {
        topheight = Math.abs(commandrect.bottom - top);
    }

    // select all spans in our commandselector, and group them by their class
    // attribute. different spans share the same class when they should be
    // linked to the same <pre> in .help
    var groupedoptions = _.groupBy(commandselector.filter(":not(.unknown)"), function(span) { return $(span).attr('helpref'); });

    // create an eslinkgroup for every group of <span>'s, these will be linked together to
    // the same <pre> in .help.
    var linkgroups = _.map(groupedoptions, function(spans, clazz) {
            var esg = new eslinkgroup(clazz, spans, mid);
            _.each(esg.links, function(l) {
                l.group = esg;
            });

            return esg;
    });

    // an array of all the links we need to make, ungrouped
    var links = _.flatten(_.pluck(linkgroups, 'links'), true),
        // the upper bounds of our drawing area
        starty = commandrect.bottom - canvastop,
        startytop = commandrect.top - canvastop;

    // links that are going left and right, in the order we'd like to process
    // them. we reverse right going links so we handle them right-to-left.
    var l = _.filter(links, function(l) { return l.goingleft; }),
        r = _.filter(links, function(l) { return !l.goingleft; }).reverse();

    // cheat a little: if all our links happen to go left, take half of them
    // to the right (this can happen if the last link happens to strech from
    // before the cutting point all the way to the end)
    if (r.length == 0) {
        var midarr = d3.round(l.length / 2);
        r = l.slice(midarr).reverse();
        l = l.slice(0, midarr);
        _.each(r, function(l) { l.goingleft = false; });
    }

    // we keep track of how many have gone right/left to calculate
    // the spacing distance between the lines
    var goingleft = 0, goingright = 0, goneleft = 0, goneright = 0;

    _.each(linkgroups, function(esg) {
        // multiple links in a group count as one in the goingleft/right
        if (_.some(esg.links, function(l) { return l.goingleft; }))
            goingleft++;

        if (_.some(esg.links, function(l) { return !l.goingleft; }))
            goingright++;
    });

    links = l.concat(r);

    // the left going links have their <pre>'s help ordered in the order the
    // <span>'s appear in .command, so the first <span> goes to
    // the first <pre>, and so on. but that means that the links are going to
    // cross each other. swapping the first and last <pre>'s will prevent that
    // (note that links can still cross each other since multiple <span>'s can
    // be linked to the same <pre>).
    //reorder(l);

    // handle all links that are not unknowns (have a <pre> in .help)
    //
    // this is hard to explain without an accompanying drawing (TODO)
    for (var i = 0; i < links.length; i++) {
        var link = links[i];

        console.log('handling', $(link.option).text());
        var rr = link.option.getBoundingClientRect(),
            spanmid = rr.left + rr.width / 2,
            rrright = rr.right - strokewidth;

        var path = new espath();

        link.paths.push(new espath().addpoint(rr.left, 0).addpoint(rrright, 5));
        link.paths.push(new espath().addpoint(rrright, 5).addpoint(rrright, 0));
        path.addpoint(rr.left + rr.width / 2, 6); // 3

        if (link.goingleft) {
            var leftmost = link.leftmost();

            // check if this is the leftmost link of the current group; for
            // those we add a line from the option to the help box. the rest of
            // the left going links in this group will connect to the top of
            // this line
            if (link == leftmost) {
                var topskip = topheight / goingleft;
                var y = topskip * goneleft + topskip;
                path.addpoint(left - ((goingleft - goneleft) * sidespace), y); // 4
                var helprect = link.help.getBoundingClientRect();
                y = helprect.top - commandrect.bottom + helprect.height / 2;
                path.addpoint(left, y);

                link.circle = {x: left+3, y: y, r: 4};

                goneleft++;
            }
            else {
                var leftmostpath = leftmost.paths[leftmost.paths.length-1];

                var p = leftmostpath.points[0],
                    pp = leftmostpath.points[1];

                path.addpoint(p["x"], pp["y"]);
            }
        }
        else {
            // handle right going links, similiarly to left
            var rightmost = link.rightmost();

            if (link == rightmost) {
                var topskip = topheight / goingright;
                var y = topskip * goneright + topskip;
                path.addpoint(right + ((goingright - goneright) * sidespace), y); // 4
                var helprect = link.help.getBoundingClientRect();
                y = helprect.top - commandrect.bottom + helprect.height / 2;
                path.addpoint(right, y);

                link.circle = {x: right-3, y: y, r: 4};

                goneright++;
            }
            else {
                var rightmostpath = rightmost.paths[rightmost.paths.length-1];
                var p = rightmostpath.points[0],
                    pp = rightmostpath.points[1];

                path.addpoint(p["x"], pp["y"]);
            }
        }

        link.paths.push(path);
    }

    // create a group for all the unknowns
    var unknowngroup = new eslinkgroup(null, commandselector.filter(".unknown").toArray(), mid);
    var linkslengthnounknown = links.length;

    $.each(unknowngroup.links, function(i, link) {
        var rr = link.option.getBoundingClientRect(),
            rrright = rr.right - strokewidth,
            nextspan = $(link.option).next()[0],
            nextlink = _.find(links, function(l) { return l.option == nextspan; });
            prevspan = $(link.option).prev()[0],
            prevlink = _.find(links, function(l) { return l.option == prevspan; });

        link.unknown = true;
        link.text = "?";

        // if there's a close link nearby to this one and it's going down, we
        // draw the this link facing up
        if ((prevlink && prevlink.directiondown && link.nearby(prevlink)) || (nextlink && nextlink.directiondown && link.nearby(nextlink))) {
            link.directiondown = false;

            link.paths.push(new espath().addpoint(rr.left, startytop).addpoint(rrright, startytop-5));
            link.paths.push(new espath().addpoint(rrright, startytop-5).addpoint(rrright, startytop));
            var rrmid = d3.round(rr.left + rr.width / 2);
            link.lines.push({x1: rrmid, y1: startytop-6, x2: rrmid, y2: startytop-5-unknownlinelength});
            link.circle = {x: rrmid, y: startytop-5-unknownlinelength-3, r: 8};
        }
        else {
            link.paths.push(new espath().addpoint(rr.left, 0).addpoint(rrright, 5));
            link.paths.push(new espath().addpoint(rrright, 5).addpoint(rrright, 0));
            var rrmid = d3.round(rr.left + rr.width / 2);
            link.lines.push({x1: rrmid, y1: 6, x2: rrmid, y2: 5+unknownlinelength});
            link.circle = {x: rrmid, y: 5+unknownlinelength+3, r: 8};
        }

        links.push(link);
    });

    if (unknowngroup.links.length > 0)
        linkgroups.push(unknowngroup);

    // d3 magic starts here
    var fline = d3.svg.line()
        .x(function(d) { return d.x; })
        .y(function(d) { return d.y; })
        .interpolate("step-before");

    // create an svg <g> for every linkgroup
    var groups = canvas.selectAll("g")
        .data(linkgroups)
        .enter().append("g");

    // create an svg <g> for every link
    var moregroups = groups.selectAll("g")
        .data(function(esg) { return esg.links; })
        .enter().append("g");

    // actually draw the lines we defined above
    moregroups.each(function(link) {
        var g = d3.select(this);

        if (link.directiondown)
            g.attr('transform', 'translate(0, ' + starty + ')');

        var paths = g.selectAll('path')
            .data(link.paths)
            .enter().append("path")
                .attr("d", function(path) { return fline(path.points); })
                .attr("stroke", link.color)
                .attr("stroke-width", strokewidth)
                .attr("fill", "none");

        var lines = g.selectAll('line')
            .data(link.lines)
            .enter().append('line')
            .attr("x1", function(line) { return line.x1; })
            .attr("y1", function(line) { return line.y1; })
            .attr("x2", function(line) { return line.x2; })
            .attr("y2", function(line) { return line.y2; })
            .attr("stroke", link.color)
            .attr("stroke-width", strokewidth)
            .attr("fill", "none");

        if (link.circle) {
            var gg = g.append('g')
                .attr("transform", "translate(" + link.circle.x + ", " + link.circle.y + ")");

            gg.append('circle')
                .attr("r", link.circle.r)
                .attr("fill", link.color);

            if (link.text) {
                gg.append("text")
                    .attr("fill", 'white')
                    .attr("text-anchor", "middle")
                    .attr("y", ".35em")
                    .attr("font-family", "Arial")
                    .text(link.text);
            }
        }
    });

    // add hover effects for the linkgroups, if we have at least one line that
    // isn't unknown
    if (linkslengthnounknown > 1) {
        var groupsnounknowns = groups.filter(function(g) { return !g.links[0].unknown; });
        groupsnounknowns.each(function(linkgroup) {
            var othergroups = groups.filter(function(other) { return linkgroup != other; });

            var s = $(linkgroup.help).add(linkgroup.options);
            console.log('s=', s);
            s.hover(
                function() {
                    /*
                     * we're highlighting a new block,
                     * disable timeout to make all blocks visible
                     **/
                    console.log('entering link group =', linkgroup, 'clearTimeout =', vtimeout);
                    clearTimeout(vtimeout);

                    // highlight all the <span>'s of the current group
                    $(linkgroup.options).css({'font-weight':'bold'});
                    // and disable transparency
                    $(linkgroup.help).add(linkgroup.options).css({opacity: 1.0});

                    // hide the lines of all other groups
                    groups.attr('visibility', function(other) {
                        return linkgroup != other ? 'hidden' : null; })

                    // and make their <span> and <pre>'s slightly transparent
                    othergroups.each(function(other) {
                        $(other.help).add(other.options).css({opacity: 0.4});
                        $(other.help).add(other.options).css({'font-weight':'normal'});
                    });
                },
                function() {
                    /*
                     * we're leaving a block,
                     * make all blocks visible unless we enter a
                     * new block within changewait ms
                     **/
                    vtimeout = setTimeout(function(){
                        $(linkgroup.options).css({'font-weight':'normal'});

                        groups.attr('visibility', function(other) {
                            return linkgroup != other ? 'visible' : null; })

                        othergroups.each(function(other) {
                            $(other.help).add(other.options).css({opacity: 1});
                        });
                    }, changewait);

                    console.log('leaving link group =', linkgroup, 'setTimeout =', vtimeout);
                }
            );
        });
    }

    prevselector = commandselector;
}

var prevselector = null;

// clear the canvas of all lines and unbind any hover events
// previously set for oldgroup
function clear() {
    $("#canvas").empty();

    if (prevselector) {
        prevselector.add(helpselector(prevselector)).unbind('mouseenter mouseleave');
        prevselector = null;
    }
}

function commandlinetourl(s) {
    if (!$.trim(s))
        return '/';

    q = $.trim(s).split(' ');
    loc = '/explain/' + q.shift();
    if (q.length)
        loc += '?' + $('<input/>', {type: 'hidden', name: 'args', value: q.join(' ')}).serialize();
    return loc;
}

// very simple adjustment of the command div font size so it doesn't overflow
function adjustcommandfontsize() {
    var commandlength = $.trim($("#command span[class^=command]").add("#command span[class^=shell]").text()).length,
        commandfontsize;

    if (commandlength > 105)
        commandfontsize = '10px';
    else if (commandlength > 95)
        commandfontsize = '12px';
    else if (commandlength > 70)
        commandfontsize = '14px';
    else if (commandlength > 60)
        commandfontsize = '16px';

    if (commandfontsize) {
        console.log('command length', commandlength, ', adjusting font size to', commandfontsize);
        $("#command").css('font-size', commandfontsize);
    }
}

var ignorekeydown = false;

function navigation() {
    // if we have more groups, show the prev/next buttons
    if (currentgroup.next) {
        var prev = $('<li><i class="icon-arrow-left icon-2"></i><span></span></li>');
        var next = $('<li><i class="icon-arrow-right icon-2"></i><span></span></li>');
        var prevnext = $('<ul class="inline" id="prevnext"><li>showing <u>all</u>, navigate:</li></ul>');
        prevnext.append(prev).append(next);
        $("#navigate").css('height', 'auto').append(prevnext);

        var nextext = next.find("span"),
            prevtext = prev.find("span"),
            currentext = prevnext.find("u");

        function grouptext(group) {
            if (group.name == 'shell')
                return 'shell syntax';
            else if (group.name != 'all')
                return group.commandselector.first().text();
            return 'all';
        }

        nextext.text(" explain " + grouptext(currentgroup.next));
        prevtext.text(" explain " + grouptext(currentgroup.prev));

        prev.click(function() {
            if (affixon())
                return;
            if (currentgroup.prev) {
                console.log('moving to the previous group (%s), current group is %s', currentgroup.prev.name, currentgroup.name);
                var oldgroup = currentgroup;
                currentgroup = currentgroup.prev
                currentext.text(grouptext(currentgroup));

                // no need to potentically call drawvisible() here since for now
                // we don't allow clicking when scrolling
                drawgrouplines(currentgroup.commandselector);

                if (!currentgroup.prev) {
                    console.log("new current group is the first group, disabling prev button");
                    prev.css({'display': 'none'});
                    prevtext.text('');
                }
                else {
                    console.log("setting prev button text to new current group prev %s", currentgroup.prev.name);

                    prevtext.text(" explain " + grouptext(currentgroup.prev));
                }

                if (currentgroup.next) {
                    next.css({'display': ''});
                    nextext.text(" explain " + grouptext(currentgroup.next));
                    console.log("setting next button text to new current group next %s", currentgroup.next.name);
                }
            }
        });

        next.click(function() {
            if (affixon())
                return;
            if (currentgroup.next) {
                console.log('moving to the next group (%s), current group is %s', currentgroup.next.name, currentgroup.name);
                var oldgroup = currentgroup;
                currentgroup = currentgroup.next
                currentext.text(grouptext(currentgroup));

                // no need to potentically call drawvisible() here since for now
                // we don't allow clicking when scrolling
                drawgrouplines(currentgroup.commandselector);

                if (!currentgroup.next) {
                    console.log("new current group is the last group, disabling next button");
                    next.css({'display': 'none'});
                    nextext.text('');
                }
                else {
                    console.log("setting next button text to new current group next %s", currentgroup.next.name);
                    nextext.text(" explain " + grouptext(currentgroup.next));
                }

                if (currentgroup.prev) {
                    prev.css({'display': ''});
                    prevtext.text(" explain " + grouptext(currentgroup.prev));
                    console.log("setting prev button text to new current group prev %s", currentgroup.prev.name);
                }
            }
        });

		// disable key navigation when the user focuses on the search box
		$("#top-search").focus(function() {
			ignorekeydown = true;
		});

		$("#top-search").blur(function() {
			ignorekeydown = false;
		});

        // bind left/right arrows as well
        $(document).keydown(function(e) {
			if (!ignorekeydown) {
				switch(e.which) {
					case 37: // left
						prev.click()
						break;
					case 39: // right
						next.click();
						break;
					default: return;
				}

				e.preventDefault();
			}
        });
    }
}

function inview(viewtop, viewbottom, $el) {
    var elemtop = $el.offset().top,
        elembottom = elemtop + $el.height(),
        elemmiddle = elemtop + ($el.height() / 2),
        elemarea = $el.width() * $el.height(),
        overlaparea = 0;

    // is the element completely outside the viewport?
    if (elembottom < viewtop || elemtop > viewbottom) {
        overlaparea = 0;
    }
    else {
        var w = $el.width(),
            h;

        // check for complete overlap
        if ((elembottom >= viewtop) && (elemtop <= viewbottom)
            && (elembottom <= viewbottom) && (elemtop >= viewtop)) {
            h = $el.height();
        }
        // check if the viewport is entirely within the element
        else if (viewtop > elemtop && viewbottom < elembottom) {
            // is the middle of the element visible?
            return (viewtop < elemmiddle && viewbottom > elemmiddle);
        }
        // check if the bottom of the element is below the viewport bottom
        else if (elembottom > viewbottom) {
            h = viewbottom - elemtop;
        }
        // the top of the element is above the viewport top
        else {
            h = elembottom - viewtop;
        }

        overlaparea = w * h;
    }

    var ratio = overlaparea / elemarea;

    //$("#coords").html("top=" + viewtop + " bottom="+viewbottom);
    //console.log($el, "top="+elemtop+" bottom="+elembottom+" area="+elemarea+" overlap="+overlaparea+" ratio="+ratio+" i="+i);
    //$("#coords").html(i+" top="+elemtop+" bottom="+elembottom+" area="+elemarea+" overlap="+overlaparea+" ratio="+ratio.toFixed(2));

    return (ratio >= 0.5)
}

function drawvisible() {
    var viewtop = $window.scrollTop(),
        viewbottom = viewtop + $window.height(),
        topspace = 80;

    viewtop += topspace;

    var visible = $("#help pre:visible").filter(function() {
        return (inview(viewtop, viewbottom, $(this)));
    });

    if (visible.length > 0) {
        //var ids = visible.map(function() { return $(this).attr('id'); });
        //$('#scroller').html(ids.toArray().join(','));

        var commandselector = optionsselector(visible);
        drawgrouplines(commandselector, {topheight: 50, hidepres: false});
    }
    else {
        //$('#scroller').html("nothing visible");
        clear();
    }
}

function draw() {
    if (affixon())
        drawvisible();
    else {
        drawgrouplines(currentgroup.commandselector);
    }
}
