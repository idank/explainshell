# -*- coding: utf-8 -*-

import textwrap

PIPELINES = textwrap.dedent('''   <b>Pipelines</b>
       A  <u>pipeline</u> is a sequence of one or more commands separated by one of the control operators <b>|</b> or <b>|&amp;</b>.  The
       format for a pipeline is:

              [<b>time</b> [<b>-p</b>]] [ ! ] <u>command</u> [ [<b>|</b>⎪<b>|&amp;</b>] <u>command2</u> ... ]

       The standard output of <u>command</u> is connected  via  a  pipe  to  the  standard  input  of  <u>command2</u>.   This
       connection  is performed before any redirections specified by the command (see <b>REDIRECTION</b> below).  If <b>|&amp;</b>
       is used, the standard error of <u>command</u> is connected to <u>command2</u>'s standard input through the pipe; it  is
       shorthand  for  <b>2&gt;&amp;1</b>  <b>|</b>.   This  implicit  redirection  of  the  standard  error  is  performed after any
       redirections specified by the command.

       The return status of a pipeline is the exit status of the last command, unless  the  <b>pipefail</b>  option  is
       enabled.   If  <b>pipefail</b>  is  enabled,  the  pipeline's return status is the value of the last (rightmost)
       command to exit with a non-zero status, or zero if all commands exit successfully.  If the reserved  word
       <b>!</b>   precedes  a  pipeline, the exit status of that pipeline is the logical negation of the exit status as
       described above.  The shell waits for all commands in the pipeline to terminate before returning a value.

       If the <b>time</b> reserved word precedes a pipeline, the elapsed as well as user and system  time  consumed  by
       its execution are reported when the pipeline terminates.  The <b>-p</b> option changes the output format to that
       specified by POSIX.  When the shell is in <u>posix</u> <u>mode</u>, it does not recognize <b>time</b> as a  reserved  word  if
       the  next  token begins with a `-'.  The <b>TIMEFORMAT</b> variable may be set to a format string that specifies
       how the timing information should be displayed; see the description of <b>TIMEFORMAT</b> under  <b>Shell</b>  <b>Variables</b>
       below.

       When the shell is in <u>posix</u> <u>mode</u>, <b>time</b> may be followed by a newline.  In this case, the shell displays the
       total user and system time consumed by the shell and its children.  The <b>TIMEFORMAT</b> variable may  be  used
       to specify the format of the time information.

       Each command in a pipeline is executed as a separate process (i.e., in a subshell).''')

OPSEMICOLON = textwrap.dedent('''       Commands separated  by  a <b>;</b> are executed sequentially; the shell waits for each command to terminate in turn.  The
       return status is the exit status of the last command executed.''')

OPBACKGROUND = textwrap.dedent('''       If a command is terminated by the control operator <b>&amp;</b>, the shell executes the command in the <u>background</u> in
       a subshell.  The shell does not wait for the command to finish, and the return  status  is  0.''')

OPANDOR = textwrap.dedent('''       AND and OR lists are sequences of one of more pipelines separated by the <b>&amp;&amp;</b>  and  <b>||</b>  control  operators,
       respectively.  AND and OR lists are executed with left associativity.  An AND list has the form

              <u>command1</u> <b>&amp;&amp;</b> <u>command2</u>

       <u>command2</u> is executed if, and only if, <u>command1</u> returns an exit status of zero.

       An OR list has the form

              <u>command1</u> <b>||</b> <u>command2</u>

       <u>command2</u>  is  executed  if and only if <u>command1</u> returns a non-zero exit status.  The return status of AND
       and OR lists is the exit status of the last command executed in the list.''')

OPERATORS = {';' : OPSEMICOLON, '&' : OPBACKGROUND, '&&' : OPANDOR, '||' : OPANDOR}

REDIRECTION = textwrap.dedent('''       Before a command is executed, its input and output may be <u>redirected</u> using a special notation interpreted
       by  the  shell.   Redirection  may  also  be used to open and close files for the current shell execution
       environment.  The following redirection operators may precede or appear anywhere within a <u>simple</u>  <u>command</u>
       or may follow a <u>command</u>.  Redirections are processed in the order they appear, from left to right.''')

REDIRECTING_INPUT = textwrap.dedent('''   <b>Redirecting</b> <b>Input</b>
       Redirection  of  input  causes  the  file  whose name results from the expansion of <u>word</u> to be opened for
       reading on file descriptor <u>n</u>, or the standard input (file descriptor 0) if <u>n</u> is not specified.

       The general format for redirecting input is:

              [<u>n</u>]<b>&lt;</b><u>word</u>''')

REDIRECTING_OUTPUT = textwrap.dedent('''   <b>Redirecting</b> <b>Output</b>
       Redirection of output causes the file whose name results from the expansion of  <u>word</u>  to  be  opened  for
       writing  on  file descriptor <u>n</u>, or the standard output (file descriptor 1) if <u>n</u> is not specified.  If the
       file does not exist it is created; if it does exist it is truncated to zero size.

       The general format for redirecting output is:

              [<u>n</u>]<b>&gt;</b><u>word</u>

       If the redirection operator is <b>&gt;</b>, and the <b>noclobber</b> option to the  <b>set</b>  builtin  has  been  enabled,  the
       redirection  will  fail if the file whose name results from the expansion of <u>word</u> exists and is a regular
       file.  If the redirection operator is <b>&gt;|</b>, or the redirection operator is <b>&gt;</b> and the  <b>noclobber</b>  option  to
       the  <b>set</b>  builtin  command  is  not  enabled, the redirection is attempted even if the file named by <u>word</u>
       exists.''')

APPENDING_REDIRECTED_OUTPUT = textwrap.dedent('''   <b>Appending</b> <b>Redirected</b> <b>Output</b>
       Redirection of output in this fashion causes the file whose name results from the expansion of <u>word</u> to be
       opened  for  appending  on  file  descriptor  <u>n</u>,  or  the standard output (file descriptor 1) if <u>n</u> is not
       specified.  If the file does not exist it is created.

       The general format for appending output is:

              [<u>n</u>]<b>&gt;&gt;</b><u>word</u>''')

REDIRECTING_OUTPUT_ERROR = textwrap.dedent('''   <b>Redirecting</b> <b>Standard</b> <b>Output</b> <b>and</b> <b>Standard</b> <b>Error</b>
       This construct allows both the standard output (file descriptor 1) and the standard  error  output  (file
       descriptor 2) to be redirected to the file whose name is the expansion of <u>word</u>.

       There are two formats for redirecting standard output and standard error:

              <b>&amp;&gt;</b><u>word</u>
       and
              <b>&gt;&amp;</b><u>word</u>

       Of the two forms, the first is preferred.  This is semantically equivalent to

              <b>&gt;</b><u>word</u> 2<b>&gt;&amp;</b>1''')

APPENDING_OUTPUT_ERROR = textwrap.dedent('''   <b>Appending</b> <b>Standard</b> <b>Output</b> <b>and</b> <b>Standard</b> <b>Error</b>
       This  construct  allows  both the standard output (file descriptor 1) and the standard error output (file
       descriptor 2) to be appended to the file whose name is the expansion of <u>word</u>.

       The format for appending standard output and standard error is:

              <b>&amp;&gt;&gt;</b><u>word</u>

       This is semantically equivalent to

              <b>&gt;&gt;</b><u>word</u> 2<b>&gt;&amp;</b>1''')

HERE_DOCUMENTS = textwrap.dedent('''   <b>Here</b> <b>Documents</b>
       This type of redirection instructs the shell  to  read  input  from  the  current  source  until  a  line
       containing  only <u>delimiter</u> (with no trailing blanks) is seen.  All of the lines read up to that point are
       then used as the standard input for a command.

       The format of here-documents is:

              <b>&lt;&lt;</b>[<b>-</b>]<u>word</u>
                      <u>here-document</u>
              <u>delimiter</u>

       No parameter expansion, command substitution, arithmetic expansion, or pathname expansion is performed on
       <u>word</u>.   If  any  characters in <u>word</u> are quoted, the <u>delimiter</u> is the result of quote removal on <u>word</u>, and
       the lines in the here-document are not expanded.  If <u>word</u> is unquoted, all lines of the here-document are
       subjected  to  parameter  expansion, command substitution, and arithmetic expansion.  In the latter case,
       the character sequence <b>\&lt;newline&gt;</b> is ignored, and <b>\</b> must be used to quote the characters <b>\</b>, <b>$</b>, and <b>`</b>.

       If the redirection operator is <b>&lt;&lt;-</b>, then all leading tab characters are stripped from input lines and the
       line  containing  <u>delimiter</u>.  This allows here-documents within shell scripts to be indented in a natural
       fashion.

   <b>Here</b> <b>Strings</b>
       A variant of here documents, the format is:

              <b>&lt;&lt;&lt;</b><u>word</u>

       The <u>word</u> is expanded and supplied to the command on its standard input.''')

REDIRECTION_KIND = {'<' : REDIRECTING_INPUT,
                   '>' : REDIRECTING_OUTPUT,
                   '>>' : APPENDING_REDIRECTED_OUTPUT,
                   '&>' : REDIRECTING_OUTPUT_ERROR,
                   '>&' : REDIRECTING_OUTPUT_ERROR,
                   '&>>' : APPENDING_OUTPUT_ERROR,
                   '<<' : HERE_DOCUMENTS,
                   '<<<' : HERE_DOCUMENTS}

_group = textwrap.dedent('''       { <u>list</u>; }
              <u>list</u> is simply executed in the current shell environment.  <u>list</u> must be terminated with a  newline
              or  semicolon.   This  is known as a <u>group</u> <u>command</u>.  The return status is the exit status of <u>list</u>.
              Note that unlike the metacharacters <b>(</b> and <b>)</b>, <b>{</b> and <b>}</b> are <u>reserved</u> <u>words</u> and  must  occur  where  a
              reserved  word  is permitted to be recognized.  Since they do not cause a word break, they must be
              separated from <u>list</u> by whitespace or another shell metacharacter.''')

_subshell = textwrap.dedent('''       (<u>list</u>) <u>list</u> is executed in a subshell environment (see <b>COMMAND</b> <b>EXECUTION</b>  <b>ENVIRONMENT</b>  below).   Variable
              assignments and builtin commands that affect the shell's environment do not remain in effect after
              the command completes.  The return status is the exit status of <u>list</u>.''')

_negate = '''If the reserved word <b>!</b> precedes a pipeline, the exit status of that pipeline is the logical negation of the
exit status as described above.'''

_if = textwrap.dedent('''       <b>if</b> <u>list</u>; <b>then</b> <u>list;</u> [ <b>elif</b> <u>list</u>; <b>then</b> <u>list</u>; ] ... [ <b>else</b> <u>list</u>; ] <b>fi</b>
              The  <b>if</b> <u>list</u> is executed.  If its exit status is zero, the <b>then</b> <u>list</u> is executed.  Otherwise, each
              <b>elif</b> <u>list</u> is executed in turn, and if its exit status is zero,  the  corresponding  <b>then</b>  <u>list</u>  is
              executed  and  the command completes.  Otherwise, the <b>else</b> <u>list</u> is executed, if present.  The exit
              status is the exit status of the last command executed, or zero if no condition tested true.''')

_for = textwrap.dedent('''       <b>for</b> <u>name</u> [ [ <b>in</b> [ <u>word</u> <u>...</u> ] ] ; ] <b>do</b> <u>list</u> ; <b>done</b>
              The  list of words following <b>in</b> is expanded, generating a list of items.  The variable <u>name</u> is set
              to each element of this list in turn, and <u>list</u> is executed each time.  If the <b>in</b> <u>word</u> is  omitted,
              the  <b>for</b>  command  executes  <u>list</u>  once  for each positional parameter that is set (see <b>PARAMETERS</b>
              below).  The return status is the exit status of the last command that executes.  If the expansion
              of  the  items  following  <b>in</b>  results  in an empty list, no commands are executed, and the return
              status is 0.''')

_whileuntil = textwrap.dedent('''       <b>while</b> <u>list-1</u>; <b>do</b> <u>list-2</u>; <b>done</b>
       <b>until</b> <u>list-1</u>; <b>do</b> <u>list-2</u>; <b>done</b>
              The <b>while</b> command continuously executes the list <u>list-2</u> as long as the last command  in  the  list
              <u>list-1</u>  returns  an  exit  status  of  zero.  The <b>until</b> command is identical to the <b>while</b> command,
              except that the test is negated; <u>list-2</u> is executed as long as the last command in <u>list-1</u>  returns
              a non-zero exit status.  The exit status of the <b>while</b> and <b>until</b> commands is the exit status of the
              last command executed in <u>list-2</u>, or zero if none was executed.''')

_select = textwrap.dedent('''       <b>select</b> <u>name</u> [ <b>in</b> <u>word</u> ] ; <b>do</b> <u>list</u> ; <b>done</b>
              The list of words following <b>in</b> is expanded, generating a list of items.  The set of expanded words
              is printed on the standard error, each preceded by a number.  If  the  <b>in</b>  <u>word</u>  is  omitted,  the
              positional  parameters are printed (see <b>PARAMETERS</b> below).  The <b>PS3</b> prompt is then displayed and a
              line read from the standard input.  If the line consists of a number corresponding to one  of  the
              displayed  words, then the value of <u>name</u> is set to that word.  If the line is empty, the words and
              prompt are displayed again.  If EOF is read, the command completes.  Any other value  read  causes
              <u>name</u> to be set to null.  The line read is saved in the variable <b>REPLY</b>.  The <u>list</u> is executed after
              each selection until a <b>break</b> command is executed.  The exit status of <b>select</b> is the exit status of
              the last command executed in <u>list</u>, or zero if no commands were executed.''')

RESERVEDWORDS = {
    '!' : _negate,
    '{' : _group,
    '}' : _group,
    '(' : _subshell,
    ')' : _subshell,
    ';' : OPSEMICOLON,
}

def _addwords(key, text, *words):
    for word in words:
        COMPOUNDRESERVEDWORDS.setdefault(key, {})[word] = text

COMPOUNDRESERVEDWORDS = {}
_addwords('if', _if, 'if', 'then', 'elif', 'else', 'fi', ';')
_addwords('for', _for, 'for', 'in', 'do', 'done', ';')
_addwords('while', _whileuntil, 'while', 'do', 'done')
_addwords('until', _whileuntil, 'until', 'do', 'done')
_addwords('select', _select, 'select', 'in', 'do', 'done')
