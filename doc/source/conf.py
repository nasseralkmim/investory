# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

project = 'investory'
copyright = '2023, Nasser Alkmim'
author = 'Nasser Alkmim'
release = '0.1'

# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

# add propper path to import the modulues
import os
import sys

sys.path.insert(0, os.path.abspath("../../"))

extensions = [
    'sphinx.ext.duration',
    'sphinx.ext.doctest',
    # autodoc allows to reference documentation with `auto*` directives — `autofunction` for instance. https://www.sphinx-doc.org/en/master/usage/extensions/autodoc.html
    'sphinx.ext.autodoc',
    # napoleon for numpy doc style https://www.sphinx-doc.org/en/master/usage/extensions/napoleon.html
    # need to call command to build the `rst` files: "sphinx-apidoc -f -o docs/api projectdir"
    # of add to the make file from sphinx
    'sphinx.ext.napoleon',
    # generate documents with the autodoc directives that reference the actual code
    'sphinx.ext.autosummary',
]

templates_path = ['_templates']
exclude_patterns = ['_build', 'Thumbs.db', '.DS_Store']


# Napoleon settings
napoleon_google_docstring = False
napoleon_numpy_docstring = True
napoleon_include_init_with_doc = False
napoleon_include_private_with_doc = False
napoleon_include_special_with_doc = True
napoleon_use_admonition_for_examples = False
napoleon_use_admonition_for_notes = False
napoleon_use_admonition_for_references = False
napoleon_use_ivar = False
napoleon_use_param = True
napoleon_use_rtype = True
napoleon_preprocess_types = False
napoleon_type_aliases = None
napoleon_attr_annotations = True

# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

html_theme = 'alabaster'
html_static_path = ['_static']


