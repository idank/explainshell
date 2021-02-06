# -*- coding: utf-8 -*-

'''manually define and add shell builtins into the store

unfortunately the bash section for builtins isn't written in a way
explainshell can understannd, so we have to resort to manually
writing these down and adding them.'''

import textwrap
from explainshell import store, config

sp = store.paragraph
so = store.option
sm = store.manpage

BUILTINS = {}

def _add(names, synopsis, options):
    name = names[0]
    # hack: fake a source man page (this breaks the outgoing links from
    # explainshell, oh well)
    names.append('bash-%s' % name)
    BUILTINS[name] = sm('bash-%s.1.gz' % name, name, synopsis, options, [(name, 20) for name in names])

_add([':'], 'the command does nothing', [so(sp(0, '''No  effect;  the command does nothing beyond expanding arguments and performing any specified redirections.  A zero
exit code  is returned.''', '', True), [], [], False, True, False)])

source = textwrap.dedent('''       <b>.</b>  <u>filename</u> [<u>arguments</u>]
       <b>source</b> <u>filename</u> [<u>arguments</u>]
              Read  and  execute  commands  from <u>filename</u> in the current shell environment and return the  exit  status  of
              the  last  command executed  from  <u>filename</u>.  If <u>filename</u> does not contain a slash, filenames in <b>PATH</b> are used
              to  find  the  directory  containing <u>filename</u>.  The file searched for in <b>PATH</b> need not be executable.  When
              <b>bash</b> is not  in  <u>posix</u>  <u>mode</u>,  the  current  directory  is searched  if no file is found in <b>PATH</b>.  If the
              <b>sourcepath</b> option to the <b>shopt</b> builtin command is turned  off,  the  <b>PATH</b>  is  not searched.   If  any
              <u>arguments</u>  are  supplied,  they  become the positional parameters when <u>filename</u> is executed.  Otherwise
              the positional  parameters  are unchanged.  The return status is the status of the last command exited within
              the  script  (0  if  no commands  are  executed), and false if <u>filename</u> is not found or cannot be read.''')
_add(['source', '.'], 'read and execute commands in the current shell', [so(sp(0, source, '', True), [], [], False, True, False)])

_add(['break'], 'exit from within a for, while, until, or select loop',
     [so(sp(0, '''If <u>n</u> is specified, break <u>n</u> levels.  <u>n</u> must be ≥ 1.  If <u>n</u> is greater than the  number  of enclosing loops, all enclosing loops are exited.  The return value is 0 unless <u>n</u> is not greater than or  equal  to 1.''', '', True), [], [], False, True, False)])

_add(['history'], 'display the  command  history  list  with  line numbers',
    [so(sp(0, '''<b>history</b> <b>[</b><u>n</u><b>]</b>
<b>history</b> <b>-c</b>
<b>history</b> <b>-d</b> <u>offset</u>
<b>history</b> <b>-anrw</b> [<u>filename</u>]
<b>history</b> <b>-p</b> <u>arg</u> [<u>arg</u> <u>...</u>]
<b>history</b> <b>-s</b> <u>arg</u> [<u>arg</u> <u>...</u>]

With no options, display the  command  history  list  with  line numbers.  Lines listed with a <b>*</b> have been modified.
An argument of <u>n</u> lists only  the  last  <u>n</u>  lines.   If  the  shell  variable <b>HISTTIMEFORMAT</b>  is  set
and  not  null,  it is used as a format string for <u>strftime</u>(3) to display the time stamp associated with each
displayed  history entry.  No intervening blank is printed between the formatted time  stamp  and  the  history
line.   If <u>filename</u>  is  supplied,  it  is  used as the name of the history file; if not, the  value  of
<b>HISTFILE</b>  is  used.''', '', True), [], [], False, True, False),
     so(sp(1, '<b>-c</b>     Clear the history list by deleting all the entries.', '', True), ['-c'], [], False, False, False),
     so(sp(2, textwrap.dedent('''              <b>-d</b> <u>offset</u>
                     Delete the history entry at position <u>offset</u>.'''), '', True), ['-d'], [], 'offset', False, False),
     so(sp(3, textwrap.dedent('''              <b>-a</b>     Append  the  ``new'' history lines (history lines entered since the beginning of the current <b>bash</b> session)
                     to  the history file.'''), '', True), ['-a'], [], False, False, False),
     so(sp(4, textwrap.dedent('''              <b>-n</b>     Read  the history lines not already read from the history file into the current  history  list.   These  are
                     lines appended  to  the history file since the beginning of the current <b>bash</b> session.'''), '', True), ['-n'], [], False, False, False),
     so(sp(5, textwrap.dedent('''              <b>-r</b>     Read the contents of the history file and append them  to the current history list.'''), '', True), ['-r'], [], False, False, False),
     so(sp(6, textwrap.dedent('''              <b>-w</b>     Write  the  current  history  list  to  the history file, overwriting the history file's contents.'''), '', True), ['-w'], [], 'filename', False, False),
     so(sp(7, textwrap.dedent('''              <b>-p</b>     Perform history substitution on the  following  <u>args</u>  and display  the  result  on  the  standard output.
                     Does not store the results in the history list.  Each <u>arg</u> must  be quoted to disable normal history expansion.'''), '', True), ['-p'], [], 'arg', True, False),
     so(sp(8, textwrap.dedent('''              <b>-s</b>     Store  the  <u>args</u>  in  the history list as a single entry.  The last command in the history list  is
                     removed  before the <u>args</u> are added.'''), '', True), ['-s'], [], 'arg', False, False)])

if __name__ == '__main__':
    import logging.config
    logging.config.dictConfig(config.LOGGING_DICT)

    s = store.store('explainshell', config.MONGO_URI)
    for m in BUILTINS.values():
        s.addmanpage(m)
