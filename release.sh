#!/bin/bash

set -e
set -x

version=$(python setup.py --version | sed 's/\.dev.*//')

status=$(git status -sz)
[ -z "$status" ] || false
git checkout master
git push 
git tag -s $version -m "Release version ${version}"
git checkout $version
git clean -fdx
#tox -epep8,py27,py34
python setup.py --version
python setup.py sdist bdist_wheel

set +x
echo
echo "release: Cotyledon ${version}"
echo
echo "SHA1sum: "
sha1sum dist/*
echo "MD5sum: "
md5sum dist/*
echo
echo "uploading..."
echo
set -x

read
git push --tags
twine upload -r pypi -s dist/cotyledon-${version}.tar.gz dist/cotyledon-${version}-py2.py3-none-any.whl
git checkout master
