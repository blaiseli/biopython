# Copyright (C) 2013 by Ben Morris (ben@bendmorris.com)
# based on code by Eric Talevich (eric.talevich@gmail.com)
# This code is part of the Biopython distribution and governed by its
# license. Please see the LICENSE file that should have been included
# as part of this package.

"""Unit tests for the NeXML and NeXMLIO modules.
"""
import os
import tempfile
import unittest

import Bio.Phylo as bp
from Bio.Phylo import NeXMLIO

# Example NeXML files
nexml_files = (
               'characters.xml',
               'edgelabels.xml',
               'meta_taxa.xml',
               'meta_types.xml',
               'nexml.xml',
               'phenoscape.xml',
               'sets.xml',
               'taxa.xml',
               'timetree.xml',
               'tolweb.xml',
               'treebase-record.xml',
               'trees-uris.xml',
               'trees.xml',
               )
tree_counts = {
               'taxa.xml': 0,
               'timetree.xml': 38,
               'phenoscape.xml': 0,
               'nexml.xml': 0,
               'meta_types.xml': 0,
               'meta_taxa.xml': 0,
               'trees.xml': 2,
               'characters.xml': 0,
               }

# Temporary file name for Writer tests below
DUMMY = tempfile.mktemp()


# ---------------------------------------------------------
# Parser tests

def _test_parse_factory(source):
    """Generate a test method for parse()ing the given source.

    The generated function extracts each phylogenetic tree using the parse()
    function and counts the total number of trees extracted.
    """
    filename = os.path.join('NeXML/', source)
    if source in tree_counts:
        count = tree_counts[source]
    else:
        count = 1

    def test_parse(self):
        trees = list(bp._io.parse(filename, 'nexml'))
        self.assertEqual(len(trees), count)

    test_parse.__doc__ = "Parse the phylogenies in %s." % source
    return test_parse


def _test_write_factory(source):
    """Tests for serialization of objects to NeXML format.

    Modifies the globally defined filenames in order to run the other parser
    tests on files (re)generated by NeXMLIO's own writer.
    """
    filename = os.path.join('NeXML/', source)

    def test_write(self):
        """Parse, rewrite and retest an example file."""
        with open(filename, 'rb') as infile:
            t1 = next(NeXMLIO.Parser(infile).parse())
        with open(DUMMY, 'w+b') as outfile:
            NeXMLIO.write([t1], outfile)

        with open(DUMMY, 'rb') as infile:
            t2 = next(NeXMLIO.Parser(infile).parse())

        def assert_property(prop_name):
            p1 = sorted([getattr(n, prop_name) for n in t1.get_terminals() if getattr(n, prop_name)])
            p2 = sorted([getattr(n, prop_name) for n in t2.get_terminals() if getattr(n, prop_name)])
            self.assertEqual(p1, p2)

        for prop_name in ('name', 'branch_length', 'confidence'):
            assert_property(prop_name)

    test_write.__doc__ = "Write and re-parse the phylogenies in %s." % source
    return test_write


class ParseTests(unittest.TestCase):
    """Tests for proper parsing of example NeXML files."""

for n, ex in enumerate(nexml_files):
    parse_test = _test_parse_factory(ex)
    parse_test.__name__ = 'test_parse_%s' % n
    setattr(ParseTests, parse_test.__name__, parse_test)


class WriterTests(unittest.TestCase):
    pass

for n, ex in enumerate(nexml_files):
    count = 1
    if ex in tree_counts:
        count = tree_counts[ex]
    if count > 0:
        write_test = _test_write_factory(ex)
        write_test.__name__ = 'test_write_%s' % n
        setattr(WriterTests, write_test.__name__, write_test)


if __name__ == '__main__':
    runner = unittest.TextTestRunner(verbosity=2)
    unittest.main(testRunner=runner)
    # Clean up the temporary file
    if os.path.exists(DUMMY):
        os.remove(DUMMY)
