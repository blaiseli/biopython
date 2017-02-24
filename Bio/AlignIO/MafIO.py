# Copyright 2011, 2012 by Andrew Sczesnak.  All rights reserved.
# Revisions Copyright 2011, 2017 by Peter Cock.  All rights reserved.
# Revisions Copyright 2014, 2015 by Adam Novak.  All rights reserved.
# Revisions Copyright 2015, 2017 by Blaise Li.  All rights reserved.
#
# This code is part of the Biopython distribution and governed by its
# license.  Please see the LICENSE file that should have been included
# as part of this package.
"""Bio.AlignIO support for the "maf" multiple alignment format.

The Multiple Alignment Format, described by UCSC, stores a series of
multiple alignments in a single file. It is suitable for whole-genome
to whole-genome alignments, metadata such as source chromosome, start
position, size, and strand can be stored.

See http://genome.ucsc.edu/FAQ/FAQformat.html#format5

You are expected to use this module via the Bio.AlignIO functions(or the
Bio.SeqIO functions if you want to work directly with the gapped sequences).

According to the MAF format documentation
(https://genome.ucsc.edu/FAQ/FAQformat.html#format5), alignment blocks are
composed of sequence lines:
-----
 Lines starting with 's' -- a sequence within an alignment block

  s hg16.chr7    27707221 13 + 158545518 gcagctgaaaaca
  s panTro1.chr6 28869787 13 + 161576975 gcagctgaaaaca
  s baboon         249182 13 +   4622798 gcagctgaaaaca
  s mm4.chr6     53310102 13 + 151104725 ACAGCTGAAAATA

 The 's' lines together with the 'a' lines define a multiple alignment. The 's'
 lines have the following fields which are defined by position rather than
 name=value pairs.

  * src -- The name of one of the source sequences for the alignment. For
  sequences that are resident in a browser assembly, the form
  'database.chromosome' allows automatic creation of links to other assemblies.
  Non-browser sequences are typically reference by the species name alone.

  * start -- The start of the aligning region in the source sequence. This is a
  zero-based number. If the strand field is '-' then this is the start relative
  to the reverse-complemented source sequence (see Coordinate Transforms).

  * size -- The size of the aligning region in the source sequence. This number
  is equal to the number of non-dash characters in the alignment text field
  below.

  * strand -- Either '+' or '-'. If '-', then the alignment is to the
  reverse-complemented source.

  * srcSize -- The size of the entire source sequence, not just the parts
  involved in the alignment.

  * text -- The nucleotides (or amino acids) in the alignment and any insertions (dashes) as well.
-----

We see that the coordinates in the MAF format are defined in terms of
zero-based start positions and aligning region sizes.

A minimal aligned region of length one and starting at first position in the
source sequence would have start == 0 and size == 1.

As we can see on this example, start + size will give one more than the
zero-based end position. We can therefore manipulate start and start + size as
python list slice boundaries.

If we want an inclusive end coordinate, we need to use end = start + size - 1:
a 1-column wide alignment would have start == end.
"""
import os
from itertools import islice

try:
    from sqlite3 import dbapi2 as _sqlite
except ImportError:
    # Not present on Jython, but should be included in Python 2.5
    # or later (unless compiled from source without its dependencies)
    # Still want to offer simple parsing/output
    _sqlite = None

from Bio.Alphabet import single_letter_alphabet
from Bio.Seq import Seq
from Bio.SeqRecord import SeqRecord
from Bio.Align import MultipleSeqAlignment
from .Interfaces import SequentialAlignmentWriter

MAFINDEX_VERSION = 1


class MafWriter(SequentialAlignmentWriter):
    """Accepts a MultipleSeqAlignment object, writes a MAF file"""
    def write_header(self):
        """Writes the MAF header"""
        self.handle.write("##maf version=1 scoring=none\n")
        self.handle.write("# generated by Biopython\n\n")

    def _write_record(self, record):
        """Writes a single SeqRecord object to an 's' line in a MAF block"""
        # convert biopython-style 1/-1 strand to MAF-style +/- strand
        if record.annotations.get("strand") == 1:
            strand = "+"
        elif record.annotations.get("strand") == -1:
            strand = "-"
        else:
            # TODO: issue warning?
            strand = "+"

        fields = ["s",
                  # In the MAF file format, spaces are not allowed in the id
                  "%-40s" % record.id.replace(" ", "_"),
                  "%15s" % record.annotations.get("start", 0),
                  "%5s" % record.annotations.get("size", len(str(record.seq).replace("-", ""))),
                  strand,
                  "%15s" % record.annotations.get("srcSize", 0),
                  str(record.seq)]
        self.handle.write("%s\n" % " ".join(fields))

    def write_alignment(self, alignment):
        """
        Writes every SeqRecord in a MultipleSeqAlignment object to its own
        MAF block (beginning with an 'a' line, containing 's' lines)
        """
        if not isinstance(alignment, MultipleSeqAlignment):
            raise TypeError("Expected an alignment object")

        if len(set([len(x) for x in alignment])) > 1:
            raise ValueError("Sequences must all be the same length")

        # We allow multiple sequences with the same IDs; for example, there may
        # be a MAF aligning the + and - strands of the same sequence together.

        # for now, use ._annotations private property, but restrict keys to those
        # specifically supported by the MAF format, according to spec
        try:
            anno = " ".join(["%s=%s" % (
                x, y) for x, y in alignment._annotations.items() if x in ("score", "pass")])
        except AttributeError:
            anno = "score=0.00"

        self.handle.write("a %s\n" % (anno,))

        recs_out = 0

        for record in alignment:
            self._write_record(record)

            recs_out += 1

        self.handle.write("\n")

        return recs_out


# Invalid function name according to pylint, but kept for compatibility
# with Bio* conventions.
def MafIterator(handle, seq_count=None, alphabet=single_letter_alphabet):
    """
    Iterates over lines in a MAF file-like object (handle), yielding
    MultipleSeqAlignment objects. SeqRecord IDs generally correspond to
    species names
    """
    in_a_bundle = False

    annotations = []
    records = []

    while True:
        # allows parsing of the last bundle without duplicating code
        try:
            line = next(handle)
        except StopIteration:
            line = ""

        if in_a_bundle:
            if line.startswith("s"):
                # add a SeqRecord to the bundle
                line_split = line.strip().split()

                if len(line_split) != 7:
                    raise ValueError("Error parsing alignment - 's' line must have 7 fields")

                # convert MAF-style +/- strand to biopython-type 1/-1
                if line_split[4] == "+":
                    strand = 1
                elif line_split[4] == "-":
                    strand = -1
                else:
                    # TODO: issue warning, set to 0?
                    strand = 1

                # s (literal), src (ID), start, size, strand, srcSize, text (sequence)
                anno = {"start": int(line_split[2]),
                        "size": int(line_split[3]),
                        "strand": strand,
                        "srcSize": int(line_split[5])}

                sequence = line_split[6]

                # interpret a dot/period to mean the same as the first sequence
                if "." in sequence:
                    if not records:
                        raise ValueError("Found dot/period in first sequence of alignment")

                    ref = str(records[0].seq)
                    new = []

                    for (letter, ref_letter) in zip(sequence, ref):
                        new.append(ref_letter if letter == "." else letter)

                    sequence = "".join(new)

                records.append(SeqRecord(
                    Seq(sequence, alphabet),
                    id=line_split[1],
                    name=line_split[1],
                    description="",
                    annotations=anno))
            elif line.startswith("i"):
                # TODO: information about what is in the aligned species DNA before
                # and after the immediately preceding "s" line
                pass
            elif line.startswith("e"):
                # TODO: information about the size of the gap between the alignments
                # that span the current block
                pass
            elif line.startswith("q"):
                # TODO: quality of each aligned base for the species.
                # Need to find documentation on this, looks like ASCII 0-9 or gap?
                # Can then store in each SeqRecord's .letter_annotations dictionary,
                # perhaps as the raw string or turned into integers / None for gap?
                pass
            elif line.startswith("#"):
                # ignore comments
                # (not sure whether comments
                # are in the maf specification, though)
                pass
            elif not line.strip():
                # end a bundle of records
                if seq_count is not None:
                    assert len(records) == seq_count

                alignment = MultipleSeqAlignment(records, alphabet)
                # TODO - Introduce an annotated alignment class?
                # See also Bio/AlignIO/FastaIO.py for same requirement.
                # For now, store the annotation a new private property:
                alignment._annotations = annotations

                yield alignment

                in_a_bundle = False

                annotations = []
                records = []
            else:
                raise ValueError("Error parsing alignment - unexpected line:\n%s" % (line,))
        elif line.startswith("a"):
            # start a bundle of records
            in_a_bundle = True
            annot_strings = line.strip().split()[1:]
            if len(annot_strings) != line.count("="):
                raise ValueError("Error parsing alignment - invalid key in 'a' line")

            annotations = dict([a_string.split("=") for a_string in annot_strings])
        elif line.startswith("#"):
            # ignore comments
            pass
        elif not line:
            break


class MafIndex(object):
    """This is used as an index for a MAF file.

    The index is a sqlite3 database that is built upon creation of the object
    if necessary, and queried when methods *search* or *get_spliced* are
    used."""
    def __init__(self, sqlite_file, maf_file, target_seqname):
        """Indexes or loads the index of a MAF file"""
        self._target_seqname = target_seqname
        self._maf_file = maf_file

        self._maf_fp = open(self._maf_file, "r")

        # if sqlite_file exists, use the existing db, otherwise index the file
        if os.path.isfile(sqlite_file):
            self._db_con = _sqlite.connect(sqlite_file)
            self._record_count = self.__check_existing_db()
        else:
            self._db_con = _sqlite.connect(sqlite_file)
            self._record_count = self.__make_new_index()

        # lastly, setup a MafIterator pointing at the open maf_file
        self._mafiter = MafIterator(self._maf_fp)

    def __check_existing_db(self):
        """Basic sanity checks upon loading an existing index"""
        try:
            idx_version = int(self._db_con.execute(
                "SELECT value FROM meta_data WHERE key = 'version'").fetchone()[0])
            if idx_version != MAFINDEX_VERSION:
                raise ValueError(
                    "Index version (%s) incompatible with this version of MafIndex" % (
                        idx_version,))

            filename = self._db_con.execute(
                "SELECT value FROM meta_data WHERE key = 'filename'").fetchone()[0]
            if filename != self._maf_file:
                raise ValueError("Index uses a different file (%s != %s)" % (
                    filename, self._maf_file))

            db_target = self._db_con.execute(
                "SELECT value FROM meta_data WHERE key = 'target_seqname'").fetchone()[0]
            if db_target != self._target_seqname:
                raise ValueError(
                    "Provided database indexed for %s, expected %s" % (
                        db_target, self._target_seqname))

            record_count = int(self._db_con.execute(
                "SELECT value FROM meta_data WHERE key = 'record_count'").fetchone()[0])
            if record_count == -1:
                raise ValueError("Unfinished/partial database provided")

            records_found = int(self._db_con.execute(
                "SELECT COUNT(*) FROM offset_data").fetchone()[0])
            if records_found != record_count:
                raise ValueError(
                    "Expected %s records, found %s.  Corrupt index?" % (
                        record_count, records_found))

            return records_found

        except (_sqlite.OperationalError, _sqlite.DatabaseError) as err:
            raise ValueError("Problem with SQLite database: %s" % err)

    def __make_new_index(self):
        """Read MAF file and generate SQLite index"""
        # make the tables
        self._db_con.execute("CREATE TABLE meta_data (key TEXT, value TEXT);")
        self._db_con.execute("INSERT INTO meta_data (key, value) VALUES ('version', 1);")
        self._db_con.execute("INSERT INTO meta_data (key, value) VALUES ('record_count', -1);")
        self._db_con.execute(
            "INSERT INTO meta_data (key, value) VALUES ('target_seqname', '%s');" % (
                self._target_seqname,))
        self._db_con.execute(
            "INSERT INTO meta_data (key, value) VALUES ('filename', '%s');" % (self._maf_file,))
        self._db_con.execute(
            "CREATE TABLE offset_data (bin INTEGER, start INTEGER, end INTEGER, offset INTEGER);")

        insert_count = 0

        # iterate over the entire file and insert in batches
        mafindex_func = self.__maf_indexer()

        while True:
            batch = list(islice(mafindex_func, 100))
            if not batch:
                break

            # batch is made from self.__maf_indexer(),
            # which yields zero-based "inclusive" start and end coordinates
            self._db_con.executemany(
                "INSERT INTO offset_data (bin, start, end, offset) VALUES (?,?,?,?);", batch)
            self._db_con.commit()
            insert_count += len(batch)

        # then make indexes on the relevant fields
        self._db_con.execute("CREATE INDEX IF NOT EXISTS bin_index ON offset_data(bin);")
        self._db_con.execute("CREATE INDEX IF NOT EXISTS start_index ON offset_data(start);")
        self._db_con.execute("CREATE INDEX IF NOT EXISTS end_index ON offset_data(end);")

        self._db_con.execute(
            "UPDATE meta_data SET value = '%s' WHERE key = 'record_count'" % (insert_count,))

        self._db_con.commit()

        return insert_count

    def __maf_indexer(self):
        """Generator function, yields index information for each bundle in the
        form of (bin, start, end, offset) tuples where start and end are
        0-based inclusive coordinates"""
        line = self._maf_fp.readline()

        while line:
            if line.startswith("a"):
                # note the offset
                offset = self._maf_fp.tell() - len(line)

                # search the following lines for a match to target_seqname
                while True:
                    line = self._maf_fp.readline()

                    if not line.strip() or line.startswith("a"):
                        # Empty line or new alignment record
                        raise ValueError("Target for indexing (%s) not found in this bundle" % (
                            self._target_seqname,))
                    elif line.startswith("s"):
                        # s (literal), src (ID), start, size, strand, srcSize, text (sequence)
                        line_split = line.strip().split()

                        if line_split[1] == self._target_seqname:
                            start = int(line_split[2])
                            size = int(line_split[3])
                            # end = int(line_split[2]) + int(line_split[3])
                            # if end - start != len(line_split[6].replace("-", "")):
                            if size != len(line_split[6].replace("-", "")):
                                raise ValueError(
                                    "Invalid length for target coordinates "
                                    "(expected %s, found %s)" % (
                                        size, len(line_split[6].replace("-", ""))))

                            # "inclusive" end position is start + length - 1
                            end = start + size - 1

                            # _ucscbin takes end-exclusive coordinates
                            yield (self._ucscbin(start, end + 1), start, end, offset)

                            break

            line = self._maf_fp.readline()

    # TODO: check coordinate correctness for the two bin-related static methods
    @staticmethod
    def _region2bin(start, end):
        """Finds bins that a region may belong to.

        Converts a region to a list of bins that it may belong to, including largest
        and smallest bins.
        """
        bins = [0, 1]

        bins.extend(range(1 + (start >> 26), 2 + ((end - 1) >> 26)))
        bins.extend(range(9 + (start >> 23), 10 + ((end - 1) >> 23)))
        bins.extend(range(73 + (start >> 20), 74 + ((end - 1) >> 20)))
        bins.extend(range(585 + (start >> 17), 586 + ((end - 1) >> 17)))

        return set(bins)

    @staticmethod
    def _ucscbin(start, end):
        """Returns the smallest bin a given region will fit into.


        Adapted from http://genomewiki.ucsc.edu/index.php/Bin_indexing_system
        """
        bin_offsets = [512 + 64 + 8 + 1, 64 + 8 + 1, 8 + 1, 1, 0]

        _bin_first_shift = 17
        _bin_next_shift = 3

        start_bin = start
        end_bin = end - 1

        start_bin >>= _bin_first_shift
        end_bin >>= _bin_first_shift

        for bin_offset in bin_offsets:
            if start_bin == end_bin:
                return bin_offset + start_bin

            start_bin >>= _bin_next_shift
            end_bin >>= _bin_next_shift

        return 0

    def _get_record(self, offset):
        """Retrieves a single MAF record located at the offset provided."""
        self._maf_fp.seek(offset)
        return next(self._mafiter)

    def search(self, starts, ends):
        """Searches index database for MAF records overlapping ranges provided.

        Returns *MultipleSeqAlignment* results in order by start, then end, then
        internal offset field.

        *starts* should be a list of 0-based start coordinates of segments in the reference.
        *ends* should be the list of the corresponding segment ends
        (in the half-open UCSC convention:
        http://genome.ucsc.edu/blog/the-ucsc-genome-browser-coordinate-counting-systems/).
        """
        # verify the provided exon coordinates
        if len(starts) != len(ends):
            raise ValueError("Every position in starts must have a match in ends")

        for exonstart, exonend in zip(starts, ends):

            exonlen = exonend - exonstart
            if exonlen < 1:
                raise ValueError("Exon coordinates (%d, %d) invalid: exon length (%d) < 1" % (
                    exonstart, exonend, exonlen))
        db_con = self._db_con

        # search for every exon
        for exonstart, exonend in zip(starts, ends):
            try:
                possible_bins = ", ".join(map(
                    str,
                    self._region2bin(exonstart, exonend)))
            except TypeError:
                raise TypeError(
                    "Exon coordinates must be integers "
                    "(start=%d, end=%d)" % (exonstart, exonend))

            # https://www.sqlite.org/lang_expr.html
            # -----
            # The BETWEEN operator
            #
            # The BETWEEN operator is logically equivalent to a pair of
            # comparisons. "x BETWEEN y AND z" is equivalent to "x>=y AND x<=z"
            # except that with BETWEEN, the x expression is only evaluated
            # once. The precedence of the BETWEEN operator is the same as the
            # precedence as operators == and != and LIKE and groups left to
            # right.
            # -----

            # We are testing overlap between the query segment and records in
            # the index, using non-strict coordinates comparisons.
            # The query segment end must be passed as end-inclusive

            result = db_con.execute(
                "SELECT DISTINCT start, end, offset FROM "
                "offset_data WHERE bin IN (%s) AND (end BETWEEN %s AND %s "
                "OR %s BETWEEN start AND end) ORDER BY start, end, "
                "offset ASC;" % (possible_bins, exonstart, exonend - 1, exonend - 1))

            rows = result.fetchall()

            # rows come from the sqlite index,
            # which should have been written using __make_new_index,
            # so rec_start and rec_end should be zero-based "inclusive" coordinates

            for rec_start, rec_end, offset in rows:
                # Iterate through hits, fetching alignments from the MAF file
                # and checking to be sure we've retrieved the expected record.

                fetched = self._get_record(int(offset))

                for record in fetched:
                    if record.id == self._target_seqname:
                        # start and size come from the maf lines
                        start = record.annotations["start"]
                        # "inclusive" end is start + length - 1
                        end = start + record.annotations["size"] - 1

                        if not (start == rec_start and end == rec_end):
                            raise ValueError(
                                "Expected %s-%s @ offset %s, found %s-%s" % (
                                    rec_start, rec_end, offset, start, end))

                yield fetched

    def get_spliced(self, starts, ends, strand=1):
        """Returns a multiple alignment of the exact sequence range provided.

        Accepts two lists of start and end positions on target_seqname, representing
        exons to be spliced in silico.  Returns a *MultipleSeqAlignment* of the
        desired sequences spliced together.

        *starts* should be a list of 0-based start coordinates of segments in the reference.
        *ends* should be the list of the corresponding segment ends
        (in the half-open UCSC convention:
        http://genome.ucsc.edu/blog/the-ucsc-genome-browser-coordinate-counting-systems/).

        To ask for the alignment portion corresponding to the first 100
        nucleotides of the reference sequence, you would use
        ``search([0], [100])``
        """
        # validate strand
        if strand not in (1, -1):
            raise ValueError("Strand must be 1 or -1, got %s" % str(strand))

        # pull all alignments that span the desired intervals
        fetched = [multiseq for multiseq in self.search(starts, ends)]

        # keep track of the expected letter count
        # (sum of lengths of [start, end) segments,
        # where [start, end) half-open)
        expected_letters = sum([end - start for start, end in zip(starts, ends)])

        # if there's no alignment, return filler for the assembly of the length given
        if len(fetched) == 0:
            return MultipleSeqAlignment([SeqRecord(Seq("N" * expected_letters),
                                                   id=self._target_seqname)])

        # find the union of all IDs in these alignments
        all_seqnames = set(
            [sequence.id for multiseq in fetched for sequence in multiseq])

        # split every record by base position
        # key: sequence name
        # value: dictionary
        #        key: position in the reference sequence
        #        value: letter(s) (including letters
        #               aligned to the "-" preceding the letter
        #               at the position in the reference, if any)
        split_by_position = dict(
            [(seq_name, {}) for seq_name in all_seqnames])

        # keep track of what the total number of (unspliced) letters should be
        total_rec_length = 0

        # track first strand encountered on the target seqname
        ref_first_strand = None

        for multiseq in fetched:
            # find the target_seqname in this MultipleSeqAlignment and use it to
            # set the parameters for the rest of this iteration
            for seqrec in multiseq:
                if seqrec.id == self._target_seqname:
                    try:
                        if ref_first_strand is None:
                            ref_first_strand = seqrec.annotations["strand"]

                            if ref_first_strand not in (1, -1):
                                raise ValueError("Strand must be 1 or -1")
                        elif ref_first_strand != seqrec.annotations["strand"]:
                            raise ValueError(
                                "Encountered strand='%s' on target seqname, "
                                "expected '%s'" % (
                                    seqrec.annotations["strand"], ref_first_strand))
                    except KeyError:
                        raise ValueError("No strand information for target seqname (%s)" % (
                            self._target_seqname,))
                    # length including gaps (i.e. alignment length)
                    rec_length = len(seqrec)
                    rec_start = seqrec.annotations["start"]
                    ungapped_length = seqrec.annotations["size"]
                    # Inclusive end in zero-based coordinates of the reference
                    rec_end = rec_start + ungapped_length - 1
                    # This is length in terms of actual letters in the reference
                    total_rec_length += ungapped_length

                    # blank out these positions for every seqname
                    for seqrec in multiseq:
                        for pos in range(rec_start, rec_end + 1):
                            split_by_position[seqrec.id][pos] = ""

                    break
            # http://psung.blogspot.fr/2007/12/for-else-in-python.html
            # https://docs.python.org/2/tutorial/controlflow.html#break-and-continue-statements-and-else-clauses-on-loops
            else:
                raise ValueError("Did not find %s in alignment bundle" % (self._target_seqname,))

            # the true, chromosome/contig/etc position in the target seqname
            real_pos = rec_start

            # loop over the alignment to fill split_by_position
            for gapped_pos in range(0, rec_length):
                for seqrec in multiseq:
                    # keep track of this position's value for the target seqname
                    if seqrec.id == self._target_seqname:
                        track_val = seqrec.seq[gapped_pos]

                    # Here, a real_pos that corresponds to just after a series of "-"
                    # in the reference will "accumulate" the letters found in other sequences
                    # in front of the "-"s
                    split_by_position[seqrec.id][real_pos] += seqrec.seq[gapped_pos]

                # increment the real_pos counter only when non-gaps are found in
                # the target_seqname, and we haven't reached the end of the record
                if track_val != "-" and real_pos < rec_end:
                    real_pos += 1

        # make sure the number of bp entries equals the sum of the record lengths
        if len(split_by_position[self._target_seqname]) != total_rec_length:
            raise ValueError(
                "Target seqname (%s) has %s records, expected %s" % (
                    self._target_seqname,
                    len(split_by_position[self._target_seqname]),
                    total_rec_length))

        # translates a position in the target_seqname sequence to its gapped length
        realpos_to_len = dict(
            # [(x, len(y)) for x, y in split_by_position[
            #     self._target_seqname].items() if len(y) > 1])
            [(
                pos,
                len(gapped_fragment)) for pos, gapped_fragment in split_by_position[
                    self._target_seqname].items() if len(gapped_fragment) > 1])

        # splice together the exons
        subseq = {}

        for seqid in all_seqnames:
            seq_split = split_by_position[seqid]
            seq_splice = []

            filler_char = "N" if seqid == self._target_seqname else "-"

            # iterate from start to end, taking bases from split_by_position when
            # they exist, using N or - for gaps when there is no alignment.
            append = seq_splice.append

            for exonstart, exonend in zip(starts, ends):
                # exonend is exclusive
                for real_pos in range(exonstart, exonend):
                    # if this seqname has this position, add it
                    if real_pos in seq_split:
                        append(seq_split[real_pos])
                    # if not, but it's in the target_seqname,
                    # add length-matched filler
                    #
                    # Can this happen?
                    # Aren't all positions in realpos_to_len also in seq_split?
                    # realpos_to_len has its keys from split_by_position[self._target_seqname]
                    # The keys in split_by_position.items()
                    # are all from range(rec_start, rec_end + 1)
                    # Yes, but this is for a given aligment block in the maf file.
                    # split_by_position.items() may have missing entries
                    # if the sequence was not that block.
                    elif real_pos in realpos_to_len:
                        append(filler_char * realpos_to_len[real_pos])
                    # it's not in either, so add a single filler character
                    else:
                        append(filler_char)

            subseq[seqid] = "".join(seq_splice)

        # make sure we're returning the right number of letters
        if len(subseq[self._target_seqname].replace("-", "")) != expected_letters:
            raise ValueError("Returning %s letters for target seqname (%s), expected %s" % (
                len(subseq[self._target_seqname].replace("-", "")),
                self._target_seqname, expected_letters))

        # check to make sure all sequences are the same length as the target seqname
        ref_subseq_len = len(subseq[self._target_seqname])

        for seqid, seq in subseq.items():
            if len(seq) != ref_subseq_len:
                raise ValueError("Returning length %s for %s, expected %s" % (
                    len(seq), seqid, ref_subseq_len))

        # finally, build a MultipleSeqAlignment object for our final sequences
        result_multiseq = []

        for seqid, seq in subseq.items():
            seq = Seq(seq)

            seq = seq if strand == ref_first_strand else seq.reverse_complement()

            result_multiseq.append(SeqRecord(
                seq,
                id=seqid,
                name=seqid,
                description=""))

        return MultipleSeqAlignment(result_multiseq)

    def __repr__(self):
        return "MafIO.MafIndex(%r, target_seqname=%r)" % (
            self._maf_fp.name,
            self._target_seqname)

    def __len__(self):
        return self._record_count
