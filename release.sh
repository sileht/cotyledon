#!/bin/bash

set -e
set -x

version=$1
[ ! "$version"] && version=$(python setup.py --version | sed 's/\.dev.*//')

status=$(git status -sz)
[ -z "$status" ] || false
git checkout master
tox -epy34,py27,pep8
git push 
git tag -s $version -m "Release version ${version}"
git checkout $version
git clean -fd
pbr_version=$(python setup.py --version)
if [ "$version" != "$pbr_version" ]; then
    echo "something goes wrong pbr version is different from the provided one. ($pbr_version != $version)"
    exit 1
fi
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
