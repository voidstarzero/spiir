#!/bin/bash
set -e 
for k in *.sqlite
do
ligolw_sqlite --verbose --tmp-space /dev/shm --extract ${k}.xml --database ${k}
ligolw_sicluster --verbose --cluster-window 1 ${k}.xml 
done

for i in H1 H2 L1
do
ligolw_add --mangle-sngl-inspiral-ids ilwd:char --verbose --output ${i}all_added.xml --verbose svd_*${i}_split_bank*.xml ../segments/injections.xml
ligolw_sicluster --verbose --cluster-window 1 ${i}all_added.xml
ligolw_sqlite --verbose --database ${i}all.sqlite --tmp-space /dev/shm --replace ${i}all_added.xml
sqlite3 ${i}all.sqlite 'DROP TABLE sim_inspiral'
ligolw_sqlite --verbose --database ${i}all.sqlite --tmp-space /dev/shm --extract ${i}all_added.xml
ligolw_add --verbose --output ${i}all.xml ../segments/injections.xml ${i}all_added.xml
ligolw_inspinjfind ${i}all.xml
ligolw_sqlite --verbose --database ${i}all.sqlite --tmp-space /dev/shm --replace ${i}all.xml
done
