#!/bin/sh

cd meta
# chmod +x postinst
version=$(grep Version control|cut -d " " -f 2)
package=$(grep Package control|cut -d " " -f 2)
mkdir -p usr/lib/enigma2/python/Plugins/Extensions/OscamSkydeStatus
cp -r ../plugin/* usr/lib/enigma2/python/Plugins/Extensions/OscamSkydeStatus
tar -cvzf data.tar.gz usr
tar -cvzf control.tar.gz control 

rm -f ../${package}_${version}_all.ipk
ar -r ../${package}_${version}_all.ipk debian-binary control.tar.gz data.tar.gz

rm -fr control.tar.gz data.tar.gz usr
