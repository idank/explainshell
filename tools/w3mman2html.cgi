#!/usr/bin/env perl

$MAN = $ENV{'W3MMAN_MAN'} || '@MAN@';
$QUERY = $ENV{'QUERY_STRING'} || $ARGV[0];
$SCRIPT_NAME = $ENV{'SCRIPT_NAME'} || $0;
$CGI = "file://$SCRIPT_NAME";
$CGI2 = "file:";
# $CGI2 = "file:///\$LIB/hlink.cgi?";
$SQUEEZE = 1;
$ENV{'PAGER'} = 'cat';

if ($QUERY =~ /\=/) {
  for (split('&', $QUERY)) {
    ($v, $q) = split('=', $_, 2);
    $query{$v} = &form_decode($q); 
  }
} else {
  $QUERY =~ s/^man=//;
  $query{"man"} = &form_decode($QUERY);
}

if ((! $query{"man"}) && (! $query{"local"})) {
  if ($query{"keyword"}) {
    $keyword = $query{"keyword"};
    $k = &html_quote($keyword);
    print <<EOF;
Content-Type: text/html

<html>
<head><title>man -k $k</title></head>
<body>
<h2>man -k <b>$k</b></h2>
<ul>
EOF
    $keyword =~ s:([^-\w\200-\377.,])::g;
    open(F, "$MAN -k $keyword 2> /dev/null |");
    @line = ();
    while(<F>) {
      chop;
      $_ = &html_quote($_);
      s/(\s+-.*)$//;
      $title = $1;
      s@(\w[\w.\-]*(\s*\,\s*\w[\w.\-]*)*)\s*(\([\dn]\w*\))@&keyword_ref($1, $3)@ge;
      print "<li>$_$title\n";
    }
    close(F);
    print <<EOF;
</ul>
</body>
</html>
EOF
    exit;
  }
  print <<EOF;
Content-Type: text/html

<html>
<head><title>man</title></head>
<body>
<form action="$CGI">
<table>
<tr><td>Manual:<td><input name=man>
<tr><td>Section:<td><input name=section>
<tr><td>Keyword:<td><input name=keyword>
<tr><td><td><input type=submit> <input type=reset>
</table>
</form>
</body>
</html>
EOF
  exit;
}

if ($query{"local"}) {
  $file = $query{"local"};
  if (! ($file =~ /^\//)) {
    $file = $query{"pwd"} . '/' . $file;
  }
  open(F, "MAN_KEEP_FORMATTING=1 $MAN -l $file 2> /dev/null |");
} else {
  $man = $query{"man"};
  if ($man =~ s/\((\w+)\)$//) {
    $section = $1;
    $man_section = "$man($1)";
  } elsif ($query{"section"}) {
    $section = $query{"section"};
    $man_section = "$man($section)";
  } else {
    $section = "";
    $man_section = "$man";
  }

  $section =~ s:([^-\w\200-\377.,])::g;
  $man =~ s:([^-\w\200-\377.,])::g;
  open(F, "MAN_KEEP_FORMATTING=1 $MAN $section $man 2> /dev/null |");
}
$ok = 0;
undef $header;
$blank = -1;
$cmd = "";
$prev = "";
while(<F>) {
  if (! defined($header)) {
    /^\s*$/ && next;
    $header = $_;
    $space = $header;
    chop $space;
    $space =~ s/\S.*//;
  } elsif ($_ eq $header) {		# delete header
    $blank = -1;
    next;
  } elsif (!/\010/ && /^$space[\w\200-\377].*\s\S/o) {	# delete footer
    $blank = -1;
    next;
  }
  if ($SQUEEZE) {
    if (/^\s*$/) {
      $blank || $blank++;
      next;
    } elsif ($blank) {
      $blank > 0 && print "\n";
      $blank = 0;
    }
  }

  s/\&/\&amp;/g;
  s/\</\&lt;/g;
  s/\>/\&gt;/g;

  s@([\200-\377].)(\010{1,2}\1)+@<b>$1</b>@g;
  s@(\&\w+;|.)(\010\1)+@<b>$1</b>@g;
  s@__\010{1,2}((\<b\>)?[\200-\377].(\</b\>)?)@<u>$1</u>@g;
  s@_\010((\<b\>)?(\&\w+\;|.)(\</b\>)?)@<u>$1</u>@g;
  s@((\<b\>)?[\200-\377].(\</b\>)?)\010{1,2}__@<u>$1</u>@g;
  s@((\<b\>)?(\&\w+\;|.)(\</b\>)?)\010_@<u>$1</u>@g;
  s@.\010(.)@$1@g;

  s@\</b\>\</u\>\<b\>_\</b\>\<u\>\<b\>@_@g;
  s@\</u\>\<b\>_\</b\>\<u\>@_@g;
  s@\</u\>\<u\>@@g;
  s@\</b\>\<b\>@@g;

  if (! $ok) {
    /^No/ && last;
    print <<EOF;
Content-Type: text/html

<html>
<head><title>man $man_section</title></head>
<body>
<pre>
EOF
    print;
    $ok = 1;
    next;
  }

  s@(http|ftp)://[\w.\-/~]+[\w/]@<a href="$&">$&</a>@g;
  s@(\W)(mailto:)?(\w[\w.\-]*\@\w[\w.\-]*\.[\w.\-]*\w)@$1<a href="mailto:$3">$2$3</a>@g;
  s@(\W)(\~?/[\w.][\w.\-/~]*)@$1 . &file_ref($2)@ge;
  s@(include(<\/?[bu]\>|\s)*\&lt;)([\w.\-/]+)@$1 . &include_ref($3)@ge;
  if ($prev && m@^\s*(\<[bu]\>)*(\w[\w.\-]*)(\</[bu]\>)*(\([\dm]\w*\))@) {
    $cmd .= "$2$4";
    $prev =~ s@(\w[\w.\-]*-)((\</[bu]\>)*\s*)$@<a href="$CGI?$cmd">$1</a>$2@;
    print $prev;
    $prev = '';
    s@^(\s*(\<[bu]\>)*)(\w[\w.\-]*)@@;
    print "$1<a href=\"$CGI?$cmd\">$3</a>";
  } elsif ($prev) {
    print $prev;
    $prev = '';
  }
  s@(\w[\w.\-]*)((\</[bu]\>)*)(\([\dm]\w*\))@<a href="$CGI?$1$4">$1</a>$2$4@g;
  if (m@(\w[\w.\-]*)-(\</[bu]\>)*\s*$@) {
    $cmd = $1;
    $prev = $_;
    next;
  }
  print;
}
if ($prev) {
  print $prev;
}
close(F);
if (! $ok) {
  if ($query{'quit'}) {
    if ($query{'local'}) {
      print STDERR "File $file not found.\n";
    } else {
      print STDERR "No manual entry for $man_section.\n";
    }
    print STDERR "No manual entry for $man_section.\n";
    print <<EOF;
w3m-control: EXIT
EOF
    exit 1;
  }
  print <<EOF;
Content-Type: text/html

<html>
<head><title>man $man_section</title></head>
<body>
<pre>
EOF
  if ($query{'local'}) {
    print "File <B>$file</B> not found.\n";
  } else {
    print "No manual entry for <B>$man_section</B>.\n";
  }
}
print <<EOF;
</pre>
</body>
</html>
EOF

sub is_command {
  local($_) = @_;
  local($p);

  (! -d && -x) || return 0;
  if (! defined(%PATH)) {
    for $p (split(":", $ENV{'PATH'})) {
      $p =~ s@/+$@@;
      $PATH{$p} = 1;
    }
  }
  s@/[^/]*$@@;
  return defined($PATH{$_});
}

sub file_ref {
  local($_) = @_;

  if (&is_command($_)) {
    ($man = $_) =~ s@.*/@@;
    return "<a href=\"$CGI?$man\">$_</a>";
  }
  if (/^\~/ || -f || -d) {
    return "<a href=\"$CGI2$_\">$_</a>";
  }
  return $_;
}

sub include_ref {
  local($_) = @_;
  local($d);

  for $d (
	"/usr/include",
	"/usr/local/include",
	"/usr/X11R6/include",
	"/usr/X11/include",
	"/usr/X/include",
	"/usr/include/X11"
  ) {
    -f "$d/$_" && return "<a href=\"$CGI2$d/$_\">$_</a>";
  }
  return $_;
}

sub keyword_ref {
  local($_, $s) = @_;
  local(@a) = ();

  for (split(/\s*,\s*/)) {
    push(@a, "<a href=\"$CGI?$_$s\">$_</a>");
  }
  return join(", ", @a) . $s;
}

sub html_quote {
  local($_) = @_;
  local(%QUOTE) = (
    '<', '&lt;',
    '>', '&gt;',
    '&', '&amp;',
    '"', '&quot;',
  );
  s/[<>&"]/$QUOTE{$&}/g;
  return $_;
}

sub form_decode {
  local($_) = @_;
  s/\+/ /g;
  s/%([\da-f][\da-f])/pack('c', hex($1))/egi;
  return $_;
}

