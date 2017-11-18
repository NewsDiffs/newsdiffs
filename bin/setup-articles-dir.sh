#!/bin/bash

mkdir -p $ARTICLES_DIR_ROOT

# Ensure world access so that we don't get permissions errors between
# the web app and scraper
chmod o+rwx $ARTICLES_DIR_ROOT