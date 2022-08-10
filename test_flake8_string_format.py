from __future__ import print_function

import ast
import codecs
import itertools
import optparse
import os
import re
import sys
import tempfile

PY26 = sys.version_info[:2] == (2, 6)

if PY26:
    import unittest2 as unittest
else:
    import unittest

from collections import defaultdict
from subprocess import Popen, PIPE

import six

import flake8_string_format


def generate_code():
    if PY26:
        working_formats = [2, 3]
    else:
        working_formats = [1, 2, 3]
    code = ['#!/usr/bin/python', '# -*- coding: utf-8 -*-', 'dummy = "line"']
    positions = []
    for variant in itertools.product(
            ['', '#', '    '], ['', 'u', 'b'], ['', '0', 'param'], ['', ':03'],
            ['', 'Before'], ['', 'After']):
        variant = list(variant)
        indented = variant[0].startswith(' ')
        for use_format in [0, 1, 2, 3]:
            # Formats:
            #  0 = just string e.g.: "foobar"
            #  1 = string assignment e.g.: buffer = "foobar"
            #  2 = format as method call e.g.: "foobar".format()
            #  3 = format as static function call e.g.: str.format("foobar")
            if use_format > 1:
                if use_format == 2:
                    fmt_code = '.format({0}42)'
                else:
                    fmt_code = ', {0}42)'
                    variant[0] += 'str.format('
                if variant[2] == 'param':
                    fmt_code = fmt_code.format('param=')
                else:
                    fmt_code = fmt_code.format('')
            else:
                fmt_code = ''
                if use_format == 1:
                    variant[0] += 'buffer = '
            if indented:
                code += ['if True:']
            code += ['{0}{1}"{4}{{{2}{3}}}{5}"{fmt}'.format(*variant, fmt=fmt_code)]
            if not variant[2] and not variant[0].strip().startswith('#') and use_format in working_formats:
                column = len(variant[0])
                if PY26:
                    expected_code = 'FMT301'
                    if use_format == 3:
                        column -= len('str.format(')
                else:
                    expected_code = 'FMT101' if use_format > 1 else 'FMT103'
                positions += [(len(code), column, expected_code)]
    return '\n'.join(code), positions

dynamic_code, dynamic_positions = generate_code()


class TestCaseBase(unittest.TestCase):

    def compare_results(self, results, expected_results):
        def format_result(result):
            return '{0}:{1}: {2}'.format(*result)

        def format_wrong_results(results, correlations=None):
            # sort using line and offset
            results = sorted(results, key=lambda result: result[:2])
            if correlations is None:
                correlations = {}

            formatted_string = ''
            for result in results:
                formatted_string += '\n\t' + format_result(result)
                if result in correlations:
                    formatted_string += ' (expected {0})'.format(format_result(correlations[result]))
            return formatted_string

        def compare_tuples_ordered(tuple1, tuple2):
            if len(tuple1) != len(tuple2):
                return False
            differences = []
            for index, (entry1, entry2) in enumerate(zip(tuple1, tuple2)):
                if entry1 != entry2:
                    differences += [index]
            return tuple(differences)


        # Extract error code part from the message
        results = [(result[0], result[1], result[2].split(' ', 1)[0])
                   for result in results]
        # Compare two results: current and expected
        # Remove all entries which can be exactly matched (those are fine)
        result_set = set(results)
        expected_result_set = set(expected_results)
        # All results must be unique
        assert len(result_set) == len(results)
        assert len(expected_result_set) == len(expected_results)
        missing_results = expected_result_set - result_set
        invalid_results = result_set - expected_result_set
        correlations = dict()
        # TODO: Try more advanced stuff like correlating two entries
        for missing_result in missing_results:
            all_candidates = defaultdict(list)
            for invalid_result in invalid_results:
                differences = compare_tuples_ordered(missing_result, invalid_result)
                assert differences  # they have to be different as per above
                if len(differences) == 1:
                    # Only single differences for now
                    all_candidates[differences] += [invalid_result]
            only_candidates = []
            for candidate_list in all_candidates.values():
                if len(candidate_list) == 1:
                    only_candidates += candidate_list
            if len(only_candidates) == 1:
                # Only one candidate is remaining, set this!
                correlations[missing_result] = only_candidates[0]
                invalid_results -= set(only_candidates)

        message = ''
        if missing_results:
            message += '\nMissing results:' + format_wrong_results(missing_results, correlations)
        if invalid_results:
            message += '\nInvalid results:' + format_wrong_results(invalid_results)
        if message:
            message = 'The reported and expected results differ:' + message
            self.fail(message)


class SimpleImportTestCase(TestCaseBase):

    def create_iterator(self, checker):
        for line, char, msg, origin in checker.run():
            yield line, char, msg
            self.assertIs(origin, flake8_string_format.StringFormatChecker)


class TestSimple(SimpleImportTestCase):

    def run_code(self, code, positions, filename):
        tree = ast.parse(code)
        checker = flake8_string_format.StringFormatChecker(tree, filename)
        self.compare_results(self.create_iterator(checker), positions)

    def test_checker(self):
        self.run_code(dynamic_code, dynamic_positions, 'fn')


class ManualFileMetaClass(type):

    _SINGLE_REGEX = re.compile(r'(FMT\d\d\d)(?: +\((\d+)\))?')
    _ERROR_REGEX = re.compile(r'^ *# Error(?:\(\+(\d+)\))?: (.*)$')

    def __new__(cls, name, bases, dct):
        prefix = os.path.join('tests', 'files')
        for filename in os.listdir(prefix):
            if filename[-3:] == '.py':
                assert re.match(r"^[A-Za-z]+[A-Za-z0-9_]*\.py", filename)
                test = cls._create_tests(prefix, filename)
                if not test is None:
                    assert test.__name__ not in dct
                    dct[test.__name__] = test

        return super(ManualFileMetaClass, cls).__new__(cls, name, bases, dct)

    @classmethod
    def _create_tests(cls, directory, filename):
        def first_find(string, searched):
            """Find the first occurrence of any string in searched."""
            first = -1
            for single in searched:
                single = string.find(single)
                if single >= 0 and (single < first or first < 0):
                    first = single
            return first

        only_filename = filename
        filename = os.path.join(directory, filename)
        with codecs.open(filename, 'r', 'utf8') as f:
            content = f.read()
        all_positions = []
        lines = content.splitlines()

        # Read first line of file to check Python version conditions
        m = re.match(r"^\s*#\s*Python(.*)", lines[0])
        if m:
            checks = []
            # Right header found, now check conditions
            for comp, v_major, v_minor in re.findall(r"\s+([<>=]=?)(\d+)\.(\d+)", m.group(1)):
                version = (int(v_major), int(v_minor))
                sys_version = sys.version_info[:2]
                if comp == "=" or comp == "==":
                    comparision = lambda: sys_version == version
                elif comp == ">":
                    comparision = lambda: sys_version > version
                elif comp == "<":
                    comparision = lambda: sys_version < version
                elif comp == ">=":
                    comparision = lambda: sys_version >= version
                elif comp == "<=":
                    comparision = lambda: sys_version <= version
                checks += [comparision]

            if not all(f() for f in checks):
                return None


        for no, line in enumerate(lines):
            match = cls._ERROR_REGEX.match(line)
            if match:
                offset = 1 if match.group(1) is None else int(match.group(1))
                line = lines[no + offset]
                for match in cls._SINGLE_REGEX.finditer(match.group(2)):
                    if match.group(2) is not None:
                        indent = int(match.group(2))
                    else:
                        indent = first_find(line, ["'", '"', 'str.format'])
                        # If r, u or b prefix, decrease indent by one
                        if line[indent] in '"\'':
                            while indent > 0 and line[indent - 1] in 'rub':
                                indent -= 1
                        assert indent >= 0

                    all_positions += [(no + offset + 1, indent, match.group(1))]

        tree = ast.parse(content)

        def defaults(self):
            self.run_test(all_positions, tree, filename)

        defaults.__name__ = str('test_{0}'.format(only_filename[:-3]))
        return defaults


@six.add_metaclass(ManualFileMetaClass)
class TestManualFiles(SimpleImportTestCase):

    """Test the manually created files in tests/files/."""

    def run_test(self, positions, tree, filename):
        checker = flake8_string_format.StringFormatChecker(tree, filename)
        self.compare_results(self.create_iterator(checker), positions)


class OutputTestCase(TestCaseBase):

    def iterator(self, messages, expected_filename):
        for msg in messages:
            match = re.match(r'([^:]+):(\d+):(\d+): (.*)', msg)
            fn, line, char, msg = match.groups()
            yield int(line), int(char) - 1, msg
            self.assertEqual(fn, expected_filename)


class Flake8CaseBase(OutputTestCase):

    def run_test(self, positions, filename, content):
        # Either stdin or file
        assert filename is None or content is None
        env = os.environ.copy()
        env['PYTHONIOENCODING'] = 'utf8'
        if content is None:
            expected_filename = filename
            stdin = None
        else:
            expected_filename = 'stdin'
            filename = '-'
            stdin = PIPE
        p = Popen(['flake8', '--select=FMT', filename], env=env,
                  stdin=stdin, stdout=PIPE, stderr=PIPE)
        # TODO: Add possibility for timeout
        stdout, stderr = p.communicate(input=content)

        stdout_lines = stdout.decode('utf8').splitlines()

        self.assertEqual(stderr, b'')
        self.compare_results(
            self.iterator(stdout_lines, expected_filename), positions)


@six.add_metaclass(ManualFileMetaClass)
class TestFlake8Files(Flake8CaseBase):

    def run_test(self, positions, tree, filename):
        """Test using stdin."""
        super(TestFlake8Files, self).run_test(positions, filename, None)


class TestFlake8StdinDynamic(Flake8CaseBase):

    def test_dynamic(self):
        self.run_test(dynamic_positions, None, dynamic_code.encode('utf8'))


@six.add_metaclass(ManualFileMetaClass)
class TestFlake8Stdin(Flake8CaseBase):

    def run_test(self, positions, tree, filename):
        """Test using stdin."""
        with open(filename, 'rb') as f:
            content = f.read()
        super(TestFlake8Stdin, self).run_test(positions, None, content)


if __name__ == '__main__':
    unittest.main()
