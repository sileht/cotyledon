#!/bin/bash

set -e
set -x

version=$1
[ ! "$version" ] && echo "missing version" && exit 1

status=$(git status -sz)
[ -z "$status" ] || false
git checkout main
[ -z "$SKIP_TESTS" ] && tox -epy312,pep8
git push
git tag $version -m "Release version ${version}"
git checkout $version
git clean -fd
tox -e build

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
twine upload -r pypi -s dist/cotyledon-${version}.tar.gz dist/cotyledon-${version}-py3-none-any.whl
git checkout main
