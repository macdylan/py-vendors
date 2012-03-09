From your libs root, do this::

    git clone --recursive git://host/python-proj-libs.git vendor

Sit back and relax while all that downloads, then proceed on your merry way.

To keep it up to date::

    pushd vendor && git pull && git submodule update --init && popd


How cbdway-libs was made
------------------------

::

    pip install -I --src='vendor/src' -r requirements/dev.txt

    # Create the .pth file so Python can find our src libs.
    find src -type d -depth 1 >> vendor.pth

    # Add all the submodules.
    for f in src/*; do
        pushd $f >/dev/null && REPO=$(git config remote.origin.url) && popd > /dev/null && git submodule add $REPO $f
    done
    git add .


Using your own lib
-------------------------

We add these lines to our manage.py file ::

    import site
    site.addsitedir('vendor')

