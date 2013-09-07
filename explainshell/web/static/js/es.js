jQuery.fn.reverse = [].reverse;

function randomcolor() {
	return 'rgb(' + (Math.floor(Math.random() * 256)) + ',' + (Math.floor(Math.random() * 256)) + ',' + (Math.floor(Math.random() * 256)) + ')'
}

var colors = ['#3182bd', '#6baed6', '#9ecae1', '#c6dbef', '#e6550d', '#fd8d3c', '#fdae6b', '#fdd0a2', '#31a354', '#74c476', '#a1d99b', '#c7e9c0', '#756bb1', '#9e9ac8', '#bcbddc', '#dadaeb', '#636363', '#969696', '#bdbdbd', '#d9d9d9'];

var shuffledcolors;

var vtimeout,
    changewait = 250;

function eslink(option, mid) {
    this.option = option;
    this.color = shuffledcolors.shift();
    this.paths = new Array();
    this.lines = new Array();
    this.circle = null;
    this.text = null;
    this.unknown = false;
    this.directiondown = true;

    if (option.getAttribute('id')) {
        this.help = $("#help-" + option.getAttribute('id'))[0];
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
    var s = $("#command span[id^=shell]");
    // if there are no 'shell' explanations, show the first (and only) command
    if (!s.length)
        currentgroup = 'command0';
    // if we have only one command in there, show everything
    else if ($("#command span[id^=command1]").length == 0)
        currentgroup = 'all';

    var commandselector, helpselector;
    if (currentgroup == 'all') {
        commandselector = $("#command span[id]");
        helpselector = $("#help pre");
    }
    else {
        commandselector = $("#command span[id^=" + currentgroup + "]");
        helpselector = $("#help pre[id^=help-" + currentgroup + "]");
    }

    return {'name' : currentgroup, 'commandselector' : commandselector,
            'helpselector' : helpselector}
}

function drawgrouplines(commandselector, helpselector) {
    shuffledcolors = _.shuffle(colors);

    var sidespace = 20, toppadding = 25, sidepadding = 15, edgedistance = 5,
        unknownlinelength = 15, strokewidth = 1;

    var canvas = d3.select("#canvas"),
        canvastop = $("#canvas")[0].getBoundingClientRect().top;

    // if the current group isn't all, hide the rest of the help, and show
    // the help of the current group
    if (currentgroup != 'all') {
        $("#help pre").not(helpselector.selector).parent().parent().hide();
        helpselector.parent().parent().show();
    }

    $("#canvas").height(helpselector.last()[0].getBoundingClientRect().bottom - canvastop);

    var commandrect = $("#command")[0].getBoundingClientRect(),
        mid = commandrect.left + commandrect.width / 2;
    helprect = $("#help")[0].getBoundingClientRect();

    var top = helprect.top - toppadding,
        left = helprect.left - sidepadding,
        right = helprect.right + sidepadding,
        topheight = Math.abs(commandrect.bottom - top);

    var links = commandselector.filter(":not(.unknown)").map(function(i, span) {
            return new eslink(span, mid);
        }),
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

    commandselector.filter(".unknown").each(function(i, span) {
        var link = new eslink(span, mid),
            rr = link.option.getBoundingClientRect(),
            rrright = rr.right - strokewidth,
            nextspan = $(span).next()[0],
            nextlink = _.find(links, function(l) { return l.option == nextspan; });
            prevspan = $(span).prev()[0],
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

    var fline = d3.svg.line()
        .x(function(d) { return d.x; })
        .y(function(d) { return d.y; })
        .interpolate("step-before");

    var groups = canvas.selectAll("g")
        .data(links)
        .enter().append("g");

    groups.each(function(link) {
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
                .text(link.text);
        }

        if (link.unknown) {
            link.option.title = "No matching help text found for this argument";
        }
    });

    if (links.length > 1) {
        groups.each(function(link) {
            var g = d3.select(this);
            var others = groups.filter(function(other) { return link != other; });

            $(link.help).add(link.option).hover(
                function() {
                    /*
                     * we're highlighting a new block,
                     * disable timeout to make all blocks visible
                     **/
                    clearTimeout(vtimeout);

                    $(link.option).css({'font-weight':'bold'});
                    $(link.option).add(link.help).css({opacity: 1.0});

                    groups.attr('visibility', function(other) {
                        return link != other ? 'hidden' : null; })

                    others.each(function(other) {
                        $(other.help).add(other.option).css({opacity: 0.4});
                        $(other.help).add(other.option).css({'font-weight':'normal'});
                    });
                },
                function() {
                    /*
                     * we're leaving a block,
                     * make all blocks visible unless we enter a
                     * new block within changewait ms
                     **/
                    vtimeout = setTimeout(function(){
                        $(link.option).css({'font-weight':'normal'});

                        groups.attr('visibility', function(other) {
                            return link != other ? 'visible' : null; })

                        others.each(function(other) {
                            $(other.help).add(other.option).css({opacity: 1});
                        });
                    }, changewait);
                }
            );
        });
    }
}

function clear() {
    $("#canvas").empty();
    $("#command span[id]").add("#help pre").unbind("hover");
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
