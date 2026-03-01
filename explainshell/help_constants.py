# -*- coding: utf-8 -*-

NO_SYNOPSIS = 'no synopsis found'

PIPELINES = '''A *pipeline* is a sequence of one or more commands separated by one of the control operators
**|** or **|&**. The format for a pipeline is:

> \\[**time** \\[**-p**]] \\[ ! ] *command1* \\[ \\[**|**|**|&**] *command2* ... ]

The standard output of
*command1* is connected via a pipe to the standard input of
*command2*. This connection is performed before any redirections specified by the
*command1*(see
**REDIRECTION** below). If **|&** is used, *command1*'s standard error, in addition to its standard output, is connected to *command2*'s standard input through the pipe; it is shorthand for **2&gt;&1 |**. This implicit redirection of the standard error to the standard output is performed after any redirections specified by *command1*.

The return status of a pipeline is the exit status of the last command, unless the **pipefail** option is enabled. If **pipefail** is enabled, the pipeline's return status is the value of the last (rightmost) command to exit with a non-zero status, or zero if all commands exit successfully. If the reserved word
**!** precedes a pipeline, the exit status of that pipeline is the logical negation of the exit status as described above. The shell waits for all commands in the pipeline to terminate before returning a value.

If the
**time** reserved word precedes a pipeline, the elapsed as well as user and system time consumed by its execution are reported when the pipeline terminates. The **-p** option changes the output format to that specified by POSIX. When the shell is in *posix mode*, it does not recognize **time** as a reserved word if the next token begins with a \\`-'. The
**TIMEFORMAT** variable may be set to a format string that specifies how the timing information should be displayed; see the description of
**TIMEFORMAT** under
**Shell Variables** below.

When the shell is in *posix mode*, **time** may be followed by a newline.  In this case, the shell displays the total user and system time consumed by the shell and its children. The
**TIMEFORMAT** variable may be used to specify the format of the time information.

Each command in a multi-command pipeline, where pipes are created, is executed in a *subshell*, which is a separate process. See
**COMMAND EXECUTION ENVIRONMENT** for a description of subshells and a subshell environment. If the **lastpipe** option is enabled using the **shopt** builtin (see the description of **shopt** below), the last element of a pipeline may be run by the shell process when job control is not active.'''

OPSEMICOLON = '''Commands separated by a
**;** are executed sequentially; the shell waits for each command to terminate in turn.  The return status is the exit status of the last command executed.'''

OPBACKGROUND = '''If a command is terminated by the control operator
**&**, the shell executes the command in the *background* in a subshell. The shell does not wait for the command to finish, and the return status is 0. These are referred to as *asynchronous* commands.'''

OPANDOR = '''AND and OR lists are sequences of one or more pipelines separated by the **&&** and **||** control operators, respectively. AND and OR lists are executed with left associativity. An AND list has the form

> *command1* **&&** *command2*

*command2* is executed if, and only if,
*command1* returns an exit status of zero (success).

An OR list has the form

> *command1* **||** *command2*

*command2* is executed if, and only if,
*command1* returns a non-zero exit status. The return status of AND and OR lists is the exit status of the last command executed in the list.'''

OPERATORS = {';' : OPSEMICOLON, '&' : OPBACKGROUND, '&&' : OPANDOR, '||' : OPANDOR}

REDIRECTION = '''Before a command is executed, its input and output may be
*redirected* using a special notation interpreted by the shell. *Redirection* allows commands' file handles to be duplicated, opened, closed, made to refer to different files, and can change the files the command reads from and writes to. Redirection may also be used to modify file handles in the current shell execution environment. The following redirection operators may precede or appear anywhere within a
*simple command* or may follow a
*command*. Redirections are processed in the order they appear, from left to right.

Each redirection that may be preceded by a file descriptor number may instead be preceded by a word of the form {*varname*}. In this case, for each redirection operator except &gt;&- and &lt;&-, the shell will allocate a file descriptor greater than or equal to 10 and assign it to *varname*. If &gt;&- or &lt;&- is preceded by {*varname*}, the value of *varname* defines the file descriptor to close. If {*varname*} is supplied, the redirection persists beyond the scope of the command, allowing the shell programmer to manage the file descriptor's lifetime manually. The **varredir\\_close** shell option manages this behavior.

In the following descriptions, if the file descriptor number is omitted, and the first character of the redirection operator is
**&lt;**, the redirection refers to the standard input (file descriptor 0).  If the first character of the redirection operator is
**&gt;**, the redirection refers to the standard output (file descriptor 1).

The word following the redirection operator in the following descriptions, unless otherwise noted, is subjected to brace expansion, tilde expansion, parameter and variable expansion, command substitution, arithmetic expansion, quote removal, pathname expansion, and word splitting. If it expands to more than one word,
**bash** reports an error.

Note that the order of redirections is significant.  For example, the command

> ls **&gt;** dirlist 2**&gt;&**1

directs both standard output and standard error to the file
*dirlist*, while the command

> ls 2**&gt;&**1 **&gt;** dirlist

directs only the standard output to file
*dirlist*, because the standard error was duplicated from the standard output before the standard output was redirected to
*dirlist*.

**Bash** handles several filenames specially when they are used in redirections, as described in the following table. If the operating system on which **bash** is running provides these special files, bash will use them; otherwise it will emulate them internally with the behavior described below.

> **/dev/fd/*fd*&zwnj;**

> > If *fd* is a valid integer, file descriptor *fd* is duplicated.

> **/dev/stdin**

> > File descriptor 0 is duplicated.

> **/dev/stdout**

> > File descriptor 1 is duplicated.

> **/dev/stderr**

> > File descriptor 2 is duplicated.

> **/dev/tcp/*host*/*port*&zwnj;**

> > If *host* is a valid hostname or Internet address, and *port* is an integer port number or service name, **bash** attempts to open the corresponding TCP socket.

> **/dev/udp/*host*/*port*&zwnj;**

> > If *host* is a valid hostname or Internet address, and *port* is an integer port number or service name, **bash** attempts to open the corresponding UDP socket.

A failure to open or create a file causes the redirection to fail.

Redirections using file descriptors greater than 9 should be used with care, as they may conflict with file descriptors the shell uses internally.

Note that the
**exec** builtin command can make redirections take effect in the current shell.'''

REDIRECTING_INPUT = '''Redirection of input causes the file whose name results from the expansion of
*word* to be opened for reading on file descriptor
*n*, or the standard input (file descriptor 0) if
*n* is not specified.

The general format for redirecting input is:

> \\[*n*]**&lt;**&zwnj;*word*'''

REDIRECTING_OUTPUT = '''Redirection of output causes the file whose name results from the expansion of
*word* to be opened for writing on file descriptor
*n*, or the standard output (file descriptor 1) if
*n* is not specified.  If the file does not exist it is created; if it does exist it is truncated to zero size.

The general format for redirecting output is:

> \\[*n*]**&gt;**&zwnj;*word*

If the redirection operator is
**&gt;**, and the
**noclobber** option to the
**set** builtin has been enabled, the redirection will fail if the file whose name results from the expansion of *word* exists and is a regular file. If the redirection operator is
**&gt;|**, or the redirection operator is
**&gt;** and the
**noclobber** option to the
**set** builtin command is not enabled, the redirection is attempted even if the file named by *word* exists.'''

APPENDING_REDIRECTED_OUTPUT = '''Redirection of output in this fashion causes the file whose name results from the expansion of
*word* to be opened for appending on file descriptor
*n*, or the standard output (file descriptor 1) if
*n* is not specified.  If the file does not exist it is created.

The general format for appending output is:

> \\[*n*]**&gt;&gt;**&zwnj;*word*'''

REDIRECTING_OUTPUT_ERROR = '''This construct allows both the standard output (file descriptor 1) and the standard error output (file descriptor 2) to be redirected to the file whose name is the expansion of
*word*.

There are two formats for redirecting standard output and standard error:

> **&&gt;**&zwnj;*word*

and

> **&gt;&**&zwnj;*word*

Of the two forms, the first is preferred. This is semantically equivalent to

> **&gt;**&zwnj;*word* 2**&gt;&**1

When using the second form, *word* may not expand to a number or **-**.  If it does, other redirection operators apply (see **Duplicating File Descriptors** below) for compatibility reasons.'''

APPENDING_OUTPUT_ERROR = '''This construct allows both the standard output (file descriptor 1) and the standard error output (file descriptor 2) to be appended to the file whose name is the expansion of
*word*.

The format for appending standard output and standard error is:

> **&&gt;&gt;**&zwnj;*word*

This is semantically equivalent to

> **&gt;&gt;**&zwnj;*word* 2**&gt;&**1

(see **Duplicating File Descriptors** below).'''

HERE_DOCUMENTS = '''This type of redirection instructs the shell to read input from the current source until a line containing only
*delimiter* (with no trailing blanks) is seen.  All of the lines read up to that point are then used as the standard input (or file descriptor *n* if *n* is specified) for a command.

The format of here-documents is:

> \\[*n*]**&lt;&lt;**\\[**-**]*word*
>         *here-document*
> *delimiter*

No parameter and variable expansion, command substitution, arithmetic expansion, or pathname expansion is performed on
*word*. If any part of
*word* is quoted, the
*delimiter* is the result of quote removal on
*word*, and the lines in the here-document are not expanded. If *word* is unquoted, all lines of the here-document are subjected to parameter expansion, command substitution, and arithmetic expansion, the character sequence
**\\&lt;newline&gt;** is ignored, and
**\\** must be used to quote the characters
**\\**,
**$**, and
**\\`**.

If the redirection operator is
**&lt;&lt;-**, then all leading tab characters are stripped from input lines and the line containing
*delimiter*. This allows here-documents within shell scripts to be indented in a natural fashion.'''

REDIRECTION_KIND = {'<' : REDIRECTING_INPUT,
                   '>' : REDIRECTING_OUTPUT,
                   '>>' : APPENDING_REDIRECTED_OUTPUT,
                   '&>' : REDIRECTING_OUTPUT_ERROR,
                   '>&' : REDIRECTING_OUTPUT_ERROR,
                   '&>>' : APPENDING_OUTPUT_ERROR,
                   '<<' : HERE_DOCUMENTS,
                   '<<<' : HERE_DOCUMENTS}

ASSIGNMENT = '''A
*variable* may be assigned to by a statement of the form

> *name*=\\[*value*]

If
*value* is not given, the variable is assigned the null string.  All
*values* undergo tilde expansion, parameter and variable expansion, command substitution, arithmetic expansion, and quote removal (see
**EXPANSION** below).  If the variable has its
**integer** attribute set, then
*value* is evaluated as an arithmetic expression even if the $((...)) expansion is not used (see
**Arithmetic Expansion** below). Word splitting and pathname expansion are not performed. Assignment statements may also appear as arguments to the
**alias**,
**declare**,
**typeset**,
**export**,
**readonly**, and
**local** builtin commands (*declaration* commands). When in *posix mode*, these builtins may appear in a command after one or more instances of the **command** builtin and retain these assignment statement properties.

In the context where an assignment statement is assigning a value to a shell variable or array index, the += operator can be used to append to or add to the variable's previous value. This includes arguments to builtin commands such as **declare** that accept assignment statements (*declaration* commands). When += is applied to a variable for which the **integer** attribute has been set, *value* is evaluated as an arithmetic expression and added to the variable's current value, which is also evaluated. When += is applied to an array variable using compound assignment (see
**Arrays** below), the variable's value is not unset (as it is when using =), and new values are appended to the array beginning at one greater than the array's maximum index (for indexed arrays) or added as additional key-value pairs in an associative array. When applied to a string-valued variable, *value* is expanded and appended to the variable's value.

A variable can be assigned the *nameref* attribute using the **-n** option to the **declare** or **local** builtin commands (see the descriptions of **declare** and **local** below) to create a *nameref*, or a reference to another variable. This allows variables to be manipulated indirectly. Whenever the nameref variable is referenced, assigned to, unset, or has its attributes modified (other than using or changing the *nameref* attribute itself), the operation is actually performed on the variable specified by the nameref variable's value. A nameref is commonly used within shell functions to refer to a variable whose name is passed as an argument to the function. For instance, if a variable name is passed to a shell function as its first argument, running

> declare -n ref=$1

inside the function creates a nameref variable **ref** whose value is the variable name passed as the first argument. References and assignments to **ref**, and changes to its attributes, are treated as references, assignments, and attribute modifications to the variable whose name was passed as **$1**. If the control variable in a **for** loop has the nameref attribute, the list of words can be a list of shell variables, and a name reference will be established for each word in the list, in turn, when the loop is executed. Array variables cannot be given the **nameref** attribute. However, nameref variables can reference array variables and subscripted array variables. Namerefs can be unset using the **-n** option to the **unset** builtin. Otherwise, if **unset** is executed with the name of a nameref variable as an argument, the variable referenced by the nameref variable will be unset.'''

_group = '''{ *list*; }

> *list* is simply executed in the current shell environment. *list* must be terminated with a newline or semicolon. This is known as a *group command*. The return status is the exit status of *list*. Note that unlike the metacharacters **(** and **)**, **{** and **}** are *reserved words* and must occur where a reserved word is permitted to be recognized.  Since they do not cause a word break, they must be separated from *list* by whitespace or another shell metacharacter.'''

_subshell = '''(*list*)

> *list* is executed in a subshell (see
> **COMMAND EXECUTION ENVIRONMENT** below for a description of a subshell environment). Variable assignments and builtin commands that affect the shell's environment do not remain in effect after the command completes.  The return status is the exit status of *list*.'''

_negate = '''If the reserved word
**!** precedes a pipeline, the exit status of that pipeline is the logical negation of the exit status as described above. The shell waits for all commands in the pipeline to terminate before returning a value.'''

_if = '''**if** *list*; **then** *list*; \\[ **elif** *list*; **then** *list*; ] ... \\[ **else** *list*; ] **fi**

> The
> **if**
> *list* is executed.  If its exit status is zero, the **then** *list* is executed.  Otherwise, each **elif** *list* is executed in turn, and if its exit status is zero, the corresponding **then** *list* is executed and the command completes.  Otherwise, the **else** *list* is executed, if present.  The exit status is the exit status of the last command executed, or zero if no condition tested true.'''

_for = '''**for** *name* \\[ \\[ **in** \\[ *word ...* ] ] ; ] **do** *list* ; **done**

> The list of words following **in** is expanded, generating a list of items. The variable *name* is set to each element of this list in turn, and *list* is executed each time. If the **in** *word* is omitted, the **for** command executes *list* once for each positional parameter that is set (see
> **PARAMETERS** below). The return status is the exit status of the last command that executes. If the expansion of the items following **in** results in an empty list, no commands are executed, and the return status is 0.'''

_whileuntil = '''**while** *list-1*; **do** *list-2*; **done**

**until** *list-1*; **do** *list-2*; **done**

> The **while** command continuously executes the list *list-2* as long as the last command in the list *list-1* returns an exit status of zero.  The **until** command is identical to the **while** command, except that the test is negated:
> *list-2* is executed as long as the last command in
> *list-1* returns a non-zero exit status. The exit status of the **while** and **until** commands is the exit status of the last command executed in *list-2*, or zero if none was executed.'''

_select = '''**select** *name* \\[ **in** *word* ] ; **do** *list* ; **done**

> The list of words following **in** is expanded, generating a list of items, and the set of expanded words is printed on the standard error, each preceded by a number.  If the **in** *word* is omitted, the positional parameters are printed (see
> **PARAMETERS** below).
> **select** then displays the
> **PS3** prompt and reads a line from the standard input. If the line consists of a number corresponding to one of the displayed words, then the value of
> *name* is set to that word. If the line is empty, the words and prompt are displayed again. If EOF is read, the **select** command completes and returns 1. Any other value read causes
> *name* to be set to null.  The line read is saved in the variable
> **REPLY**. The
> *list* is executed after each selection until a
> **break** command is executed. The exit status of
> **select** is the exit status of the last command executed in
> *list*, or zero if no commands were executed.'''

RESERVED_WORDS = {
    '!' : _negate,
    '{' : _group,
    '}' : _group,
    '(' : _subshell,
    ')' : _subshell,
    ';' : OPSEMICOLON,
}

def _addwords(key, text, *words):
    for word in words:
        COMPOUND_RESERVED_WORDS.setdefault(key, {})[word] = text

COMPOUND_RESERVED_WORDS = {}
_addwords('if', _if, 'if', 'then', 'elif', 'else', 'fi', ';')
_addwords('for', _for, 'for', 'in', 'do', 'done', ';')
_addwords('while', _whileuntil, 'while', 'do', 'done', ';')
_addwords('until', _whileuntil, 'until', 'do', 'done')
_addwords('select', _select, 'select', 'in', 'do', 'done')

_function = '''A shell function is an object that is called like a simple command and executes a compound command with a new set of positional parameters. Shell functions are declared as follows:

*fname* () *compound-command* \\[*redirection*]

**function** *fname* \\[()] *compound-command* \\[*redirection*]

> This defines a function named *fname*. The reserved word **function** is optional. If the **function** reserved word is supplied, the parentheses are optional. The *body* of the function is the compound command
> *compound-command* (see **Compound Commands** above). That command is usually a *list* of commands between { and }, but may be any command listed under **Compound Commands** above. If the **function** reserved word is used, but the parentheses are not supplied, the braces are recommended. *compound-command* is executed whenever *fname* is specified as the name of a simple command. When in *posix mode*, *fname* must be a valid shell *name* and may not be the name of one of the POSIX *special builtins*. In default mode, a function name can be any unquoted shell word that does not contain **$**. Any redirections (see
> **REDIRECTION** below) specified when a function is defined are performed when the function is executed. The exit status of a function definition is zero unless a syntax error occurs or a readonly function with the same name already exists. When executed, the exit status of a function is the exit status of the last command executed in the body.  (See
> **FUNCTIONS** below.)'''

_function_call = 'call shell function %r'
_functionarg = 'argument for shell function %r'

COMMENT = '''In a non-interactive shell, or an interactive shell in which the
**interactive\\_comments** option to the
**shopt** builtin is enabled (see
**SHELL BUILTIN COMMANDS** below), a word beginning with
**#** causes that word and all remaining characters on that line to be ignored.  An interactive shell without the
**interactive\\_comments** option enabled does not allow comments.  The
**interactive\\_comments** option is on by default in interactive shells.'''

parameters = {
        '*' : 'star',
        '@' : 'at',
        '#' : 'pound',
        '?' : 'question',
        '-' : 'hyphen',
        '$' : 'dollar',
        '!' : 'exclamation',
        '0' : 'zero',
        '_' : 'underscore',
}
