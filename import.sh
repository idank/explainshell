#!/bin/bash
# vim: noai:ts=2:sw=2

HOME=/opt/webapp
MANPAGES=${HOME}/manpages
DISTROS=$(ls -d ${MANPAGES}/* | awk -F/ '{print $NF}' | tr '\n' ' '; echo)

usage() {
  cat <<-HELPMSG
		usage $0 DISTRO

		DISTRO: ${DISTROS}
	HELPMSG
}

help_wanted() {
	[[ $# -ne 1 ]] || \
	( [[ $# -eq 1 ]] && [[ $1 = '-?' ]] || [[ $1 = '-h' ]] || [[ $1 = '--help' ]] )
}

if help_wanted "$@"; then
	usage
	exit -1
fi


cd ${HOME}
export PYTHONPATH=. 
if [[ " ${DISTROS[@]} " =~ " ${1} " ]] ; then
	find ${MANPAGES}/${1} -type f -exec python explainshell/manager.py --log info '{}' \; -print
else
	echo "${1} doesn't exist under ${MANPAGES}."
	exit -1
fi

