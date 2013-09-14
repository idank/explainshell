jQuery.fn.reverse = [].reverse;

function randomcolor() {
	return 'rgb(' + (Math.floor(Math.random() * 256)) + ',' + (Math.floor(Math.random() * 256)) + ',' + (Math.floor(Math.random() * 256)) + ')'
}

var colors = ['#3182bd', '#6baed6', '#9ecae1', '#c6dbef', '#e6550d', '#fd8d3c', '#fdae6b', '#fdd0a2', '#31a354', '#74c476', '#a1d99b', '#c7e9c0', '#756bb1', '#9e9ac8', '#bcbddc', '#dadaeb', '#636363', '#969696', '#bdbdbd', '#d9d9d9'];

var shuffledcolors;

var vtimeout,
    changewait = 250;

function eslinkgroup(clazz, options, mid) {
    var color = shuffledcolors.shift();
    this.links = options.map(function(option) { return new eslink(clazz, option, mid, color); });
    this.options = _.pluck(this.links, 'option');
    this.help = _.pluck(this.links, 'help');
}

function eslink(clazz, option, mid, color) {
    this.option = option;
    this.color = color;
    this.paths = new Array();
    this.lines = new Array();
    this.circle = null;
    this.text = null;
    this.unknown = false;
    this.directiondown = true;

    if (clazz) {
        this.help = $(".help-" + clazz)[0];
        var rr = option.getBoundingClientRect();
        var rrmid = rr.left + rr.width / 2;
        this.goingleft = rrmid <= mid;

        $(this.help).css("border-color", this.color)
                    .css("background-color", "white");
	}
}

eslink.prototype.nearby = function(other) {
    var closeness = 5,
        r = this.option.getBoundingClientRect(), rr = other.option.getBoundingClientRect();

    return Math.abs(r.right - rr.left) <= closeness || Math.abs(r.left - rr.right) <= closeness;
}

function espath() {
    this.points = new Array();
}

espath.prototype.addpoint = function(x, y) {
    this.points.push({"x": d3.round(x), "y": d3.round(y)});
    return this;
}

function swapNodes(a, b) {
    var aparent= a.parentNode;
    var asibling= a.nextSibling===b? a : a.nextSibling;
    b.parentNode.insertBefore(a, b);
    aparent.insertBefore(b, asibling);
}

function reorder(lefteslinks) {
    var help = _.pluck(lefteslinks, 'help');

    for (var i = 0, j = help.length - 1; i < Math.floor(help.length / 2) && i != j; i++, j = help.length - 1 - i) {
        var h = help[i],
            hh = help[j];

        swapNodes(h, hh);
    }
}

// initialize the lines logic, deciding which group of elements should be displayed
// returns the name of the group (with 'all' meaning draw everything) and two
// selectors: one selects which spans in #command and the other selects their
// matching help text in #help
function initialize() {
    var currentgroup = 'shell';
    var s = $("#command span[class^=shell]");
    // if there are no 'shell' explanations, show the first (and only) command
    if (!s.length)
        currentgroup = 'command0';
    // if we have only one command in there, show everything
    else if ($("#command span[class^=command1]").length == 0)
        currentgroup = 'all';

    var commandselector, helpselector, head = {'name' : currentgroup};

    if (currentgroup == 'all') {
        commandselector = $("#command span[class]").filter(":not(.dropdown)");
        helpselector = $("#help pre");
    }
    else {
        commandselector = $("#command span[class^=" + currentgroup + "]");
        helpselector = $("#help pre[class^=help-" + currentgroup + "]");

        // construct a doubly linked list of previous/next groups. this is used
        // by the navigation buttons to move between groups
        if (currentgroup == 'shell')
        {
            var i = 0, g = "command" + i, s = $("#command span[class^=" + g + "]"),
                prev = head;
            while (s.length > 0) {
                var curr = {'name' : g, 'commandselector' : s,
                         'helpselector' : $("#help pre[class^=help-" + g + "]")}

                curr['prev'] = prev
                prev['next'] = curr;
                prev = curr;
                i++;
                g = "command" + i;
                s = $("#command span[class^=" + g + "]")
            }
        }
    }

    head['commandselector'] = commandselector;
    head['helpselector'] = helpselector;

    return head;
}

function drawgrouplines(commandselector, helpselector) {
    shuffledcolors = _.shuffle(colors);

    var sidespace = 20, toppadding = 25, sidepadding = 15, edgedistance = 5,
        unknownlinelength = 15, strokewidth = 1;

    var canvas = d3.select("#canvas"),
        canvastop = $("#canvas")[0].getBoundingClientRect().top;

    // if the current group isn't all, hide the rest of the help, and show
    // the help of the current group
    if (currentgroup.name != 'all') {
        $("#help pre").not(helpselector.selector).parent().parent().hide();
        helpselector.parent().parent().show();
    }

    if (helpselector.length > 0)
        $("#canvas").height(helpselector.last()[0].getBoundingClientRect().bottom - canvastop);

    var commandrect = $("#command")[0].getBoundingClientRect(),
        mid = commandrect.left + commandrect.width / 2;
    helprect = $("#help")[0].getBoundingClientRect();

    var top = helprect.top - toppadding,
        left = helprect.left - sidepadding,
        right = helprect.right + sidepadding,
        topheight = Math.abs(commandrect.bottom - top);

    var groupedoptions = _.groupBy(commandselector.filter(":not(.unknown)"), function(span) { return $(span).attr('class'); });

    var linkgroups = _.map(groupedoptions, function(spans, clazz) {
            return new eslinkgroup(clazz, spans, mid);
    });

    var links = _.flatten(_.pluck(linkgroups, 'links'), true),
        starty = commandrect.bottom - canvastop,
        startytop = commandrect.top - canvastop;

    var l = _.filter(links, function(l) { return l.goingleft; }),
        r = _.filter(links, function(l) { return !l.goingleft; }).reverse();

    if (r.length == 0) {
        var midarr = d3.round(l.length / 2);
        r = l.slice(midarr).reverse();
        l = l.slice(0, midarr);
        _.each(r, function(l) { l.goingleft = false; });
    }

    var goingleft = l.length, goingright = links.length - goingleft,
        goneleft = 0, goneright = 0;

    links = l.concat(r);

    reorder(l);

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
            var topskip = topheight / goingright;
            var y = topskip * goneright + topskip;
            path.addpoint(right + ((goingright - goneright) * sidespace), y); // 4
            var helprect = link.help.getBoundingClientRect();
            y = helprect.top - commandrect.bottom + helprect.height / 2;
            path.addpoint(right, y);

            link.circle = {x: right-3, y: y, r: 4};

            goneright++;
        }

        link.paths.push(path);
    }

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

    var fline = d3.svg.line()
        .x(function(d) { return d.x; })
        .y(function(d) { return d.y; })
        .interpolate("step-before");

    var groups = canvas.selectAll("g")
        .data(linkgroups)
        .enter().append("g");

    var moregroups = groups.selectAll("g")
        .data(function(esg) { return esg.links; })
        .enter().append("g");

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

        if (link.unknown) {
            link.option.title = "No matching help text found for this argument";
        }
    });

    if (linkslengthnounknown > 1) {
        groups.each(function(linkgroup) {
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

                    $(linkgroup.options).css({'font-weight':'bold'});
                    $(linkgroup.help).add(linkgroup.options).css({opacity: 1.0});

                    groups.attr('visibility', function(other) {
                        return linkgroup != other ? 'hidden' : null; })

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
}

function clear(oldgroup) {
    $("#canvas").empty();
    oldgroup.commandselector.add(oldgroup.helpselector).unbind('mouseenter mouseleave');
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

    if (commandlength > 115)
        commandfontsize = '10px';
    else if (commandlength > 105)
        commandfontsize = '12px';
    else if (commandlength > 85)
        commandfontsize = '14px';
    else if (commandlength > 70)
        commandfontsize = '16px';

    if (commandfontsize) {
        console.log('command length', commandlength, ', adjusting font size to', commandfontsize);
        $("#command").css('font-size', commandfontsize);
    }
}
