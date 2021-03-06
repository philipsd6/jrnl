#!/usr/bin/env python
# encoding: utf-8

from __future__ import absolute_import, unicode_literals
from . import Entry
from . import util
from . import time
import os
import sys
import codecs
import re
from datetime import datetime
import logging

log = logging.getLogger(__name__)


class Tag(object):
    def __init__(self, name, count=0):
        self.name = name
        self.count = count

    def __str__(self):
        return self.name

    def __repr__(self):
        return "<Tag '{}'>".format(self.name)


class Journal(object):
    def __init__(self, name='default', **kwargs):
        self.config = {
            'journal': "journal.txt",
            'encrypt': False,
            'default_hour': 9,
            'default_minute': 0,
            'timeformat': "%Y-%m-%d %H:%M",
            'tagsymbols': '@',
            'highlight': True,
            'linewrap': 80,
            'indent_character': '|',
        }
        self.config.update(kwargs)
        # Set up date parser
        self.search_tags = None  # Store tags we're highlighting
        self.name = name

    def __len__(self):
        """Returns the number of entries"""
        return len(self.entries)

    def __iter__(self):
        """Iterates over the journal's entries."""
        return (entry for entry in self.entries)

    @classmethod
    def from_journal(cls, other):
        """Creates a new journal by copying configuration and entries from
        another journal object"""
        new_journal = cls(other.name, **other.config)
        new_journal.entries = other.entries
        log.debug("Imported %d entries from %s to %s", len(new_journal), other.__class__.__name__, cls.__name__)
        return new_journal

    def import_(self, other_journal_txt):
        self.entries = list(frozenset(self.entries) | frozenset(self._parse(other_journal_txt)))
        self.sort()

    def open(self, filename=None):
        """Opens the journal file defined in the config and parses it into a list of Entries.
        Entries have the form (date, title, body)."""
        filename = filename or self.config['journal']

        if not os.path.exists(filename):
            util.prompt("[Journal '{0}' created at {1}]".format(self.name, filename))
            self._create(filename)

        text = self._load(filename)
        self.entries = self._parse(text)
        self.sort()
        log.debug("opened %s with %d entries", self.__class__.__name__, len(self))
        return self

    def write(self, filename=None):
        """Dumps the journal into the config file, overwriting it"""
        filename = filename or self.config['journal']
        text = "\n".join([e.__unicode__() for e in self.entries])
        self._store(filename, text)

    def _load(self, filename):
        raise NotImplementedError

    def _store(self, filename, text):
        raise NotImplementedError

    @classmethod
    def _create(cls, filename):
        raise NotImplementedError

    def _parse(self, journal_txt):
        """Parses a journal that's stored in a string and returns a list of entries"""
        # Initialise our current entry
        entries = []
        date_blob_re = re.compile("(?:^|\n)\[([^\\]]+)\] ")
        last_entry_pos = 0
        for match in date_blob_re.finditer(journal_txt):
            date_blob = match.groups()[0]
            new_date = time.parse(date_blob)
            if new_date:
                if entries:
                    entries[-1].text = journal_txt[last_entry_pos:match.start()]
                last_entry_pos = match.end()
                entries.append(Entry.Entry(self, date=new_date))
        # Finish the last entry
        if entries:
            entries[-1].text = journal_txt[last_entry_pos:]

        for entry in entries:
            entry._parse_text()
        return entries

    def __unicode__(self):
        return self.pprint()

    def pprint(self, short=False):
        """Prettyprints the journal's entries"""
        sep = "\n"
        pp = sep.join([e.pprint(short=short) for e in self.entries])
        if self.config['highlight']:  # highlight tags
            if self.search_tags:
                for tag in self.search_tags:
                    tagre = re.compile(re.escape(tag), re.IGNORECASE)
                    pp = re.sub(tagre,
                                lambda match: util.colorize(match.group(0)),
                                pp, re.UNICODE)
            else:
                pp = re.sub(
                    Entry.Entry.tag_regex(self.config['tagsymbols']),
                    lambda match: util.colorize(match.group(0)),
                    pp
                )
        return pp

    def __repr__(self):
        return "<Journal with {0} entries>".format(len(self.entries))

    def sort(self):
        """Sorts the Journal's entries by date"""
        self.entries = sorted(self.entries, key=lambda entry: entry.date)

    def limit(self, n=None):
        """Removes all but the last n entries"""
        if n:
            self.entries = self.entries[-n:]

    @property
    def tags(self):
        """Returns a set of tuples (count, tag) for all tags present in the journal."""
        # Astute reader: should the following line leave you as puzzled as me the first time
        # I came across this construction, worry not and embrace the ensuing moment of enlightment.
        tags = [tag
                for entry in self.entries
                for tag in set(entry.tags)]
        # To be read: [for entry in journal.entries: for tag in set(entry.tags): tag]
        tag_counts = set([(tags.count(tag), tag) for tag in tags])
        return [Tag(tag, count=count) for count, tag in sorted(tag_counts)]

    def filter(self, tags=[], start_date=None, end_date=None, starred=False, strict=False, short=False):
        """Removes all entries from the journal that don't match the filter.

        tags is a list of tags, each being a string that starts with one of the
        tag symbols defined in the config, e.g. ["@John", "#WorldDomination"].

        start_date and end_date define a timespan by which to filter.

        starred limits journal to starred entries

        If strict is True, all tags must be present in an entry. If false, the
        entry is kept if any tag is present."""
        self.search_tags = set([tag.lower() for tag in tags])
        end_date = time.parse(end_date, inclusive=True)
        start_date = time.parse(start_date)

        # If strict mode is on, all tags have to be present in entry
        tagged = self.search_tags.issubset if strict else self.search_tags.intersection
        result = [
            entry for entry in self.entries
            if (not tags or tagged(entry.tags))
            and (not starred or entry.starred)
            and (not start_date or entry.date >= start_date)
            and (not end_date or entry.date <= end_date)
        ]

        self.entries = result

    def new_entry(self, raw, date=None, sort=True):
        """Constructs a new entry from some raw text input.
        If a date is given, it will parse and use this, otherwise scan for a date in the input first."""

        raw = raw.replace('\\n ', '\n').replace('\\n', '\n')
        starred = False
        # Split raw text into title and body
        sep = re.search("\n|[\?!.]+ +\n?", raw)
        first_line = raw[:sep.end()].strip() if sep else raw
        starred = False

        if not date:
            colon_pos = first_line.find(": ")
            if colon_pos > 0:
                date = time.parse(raw[:colon_pos], default_hour=self.config['default_hour'], default_minute=self.config['default_minute'])
                if date:  # Parsed successfully, strip that from the raw text
                    starred = raw[:colon_pos].strip().endswith("*")
                    raw = raw[colon_pos + 1:].strip()
        starred = starred or first_line.startswith("*") or first_line.endswith("*")
        if not date:  # Still nothing? Meh, just live in the moment.
            date = time.parse("now")
        entry = Entry.Entry(self, date, raw, starred=starred)
        entry.modified = True
        self.entries.append(entry)
        if sort:
            self.sort()
        return entry

    def editable_str(self):
        """Turns the journal into a string of entries that can be edited
        manually and later be parsed with eslf.parse_editable_str."""
        return "\n".join([e.__unicode__() for e in self.entries])

    def parse_editable_str(self, edited):
        """Parses the output of self.editable_str and updates it's entries."""
        mod_entries = self._parse(edited)
        # Match those entries that can be found in self.entries and set
        # these to modified, so we can get a count of how many entries got
        # modified and how many got deleted later.
        for entry in mod_entries:
            entry.modified = not any(entry == old_entry for old_entry in self.entries)
        self.entries = mod_entries


class PlainJournal(Journal):
    @classmethod
    def _create(cls, filename):
        with codecs.open(filename, "a", "utf-8"):
            pass

    def _load(self, filename):
        with codecs.open(filename, "r", "utf-8") as f:
            return f.read()

    def _store(self, filename, text):
        with codecs.open(filename, 'w', "utf-8") as f:
            f.write(text)


class LegacyJournal(Journal):
    """Legacy class to support opening journals formatted with the jrnl 1.x
    standard. Main difference here is that in 1.x, timestamps were not cuddled
    by square brackets. You'll not be able to save these journals anymore."""
    def _load(self, filename):
        with codecs.open(filename, "r", "utf-8") as f:
            return f.read()

    def _parse(self, journal_txt):
        """Parses a journal that's stored in a string and returns a list of entries"""
        # Entries start with a line that looks like 'date title' - let's figure out how
        # long the date will be by constructing one
        date_length = len(datetime.today().strftime(self.config['timeformat']))

        # Initialise our current entry
        entries = []
        current_entry = None
        for line in journal_txt.splitlines():
            line = line.rstrip()
            try:
                # try to parse line as date => new entry begins
                new_date = datetime.strptime(line[:date_length], self.config['timeformat'])

                # parsing successful => save old entry and create new one
                if new_date and current_entry:
                    entries.append(current_entry)

                if line.endswith("*"):
                    starred = True
                    line = line[:-1]
                else:
                    starred = False

                current_entry = Entry.Entry(self, date=new_date, text=line[date_length + 1:], starred=starred)
            except ValueError:
                # Happens when we can't parse the start of the line as an date.
                # In this case, just append line to our body.
                if current_entry:
                    current_entry.text += line + u"\n"

        # Append last entry
        if current_entry:
            entries.append(current_entry)
        for entry in entries:
            entry._parse_text()
        return entries


def open_journal(name, config, legacy=False):
    """
    Creates a normal, encrypted or DayOne journal based on the passed config.
    If legacy is True, it will open Journals with legacy classes build for
    backwards compatibility with jrnl 1.x
    """
    config = config.copy()
    config['journal'] = os.path.expanduser(os.path.expandvars(config['journal']))

    if os.path.isdir(config['journal']):
        if config['journal'].strip("/").endswith(".dayone") or "entries" in os.listdir(config['journal']):
            from . import DayOneJournal
            return DayOneJournal.DayOne(**config).open()
        else:
            util.prompt(u"[Error: {0} is a directory, but doesn't seem to be a DayOne journal either.".format(config['journal']))
            sys.exit(1)

    if not config['encrypt']:
        if legacy:
            return LegacyJournal(name, **config).open()
        return PlainJournal(name, **config).open()
    else:
        from . import EncryptedJournal
        if legacy:
            return EncryptedJournal.LegacyEncryptedJournal(name, **config).open()
        return EncryptedJournal.EncryptedJournal(name, **config).open()
