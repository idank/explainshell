#!/bin/bash


if [[ (-x /usr/bin/mongorestore) && (-d /opt/webapp/dump) ]] ; then
  /usr/bin/mongorestore -h db /opt/webapp/dump
else
  echo "Missing mongo-tools or dump files. Unable to restore classifiers."
  exit -1
fi

/usr/local/bin/python runserver.py make serve
