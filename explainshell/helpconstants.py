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
