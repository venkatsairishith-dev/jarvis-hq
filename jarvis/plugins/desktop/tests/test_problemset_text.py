"""D77: splitting a statement file into problems and pulling out the sample cases (plugins/desktop/problemset_text.py over config/problemset.json).
Realistic fixtures: numbered lists, 'Q1.', 'Problem 2:', Hindi and Telugu headers kept as written, tables, a picture read by a fake OCR, mixed files."""
import json

import pytest

from jarvis.plugins.desktop import problemset_text as T

R = T.load_rules()


def split(text):
    return T.split_problems(text, R)


FIXTURES = {
    "problem_word": ("Problem 1: Sum of two numbers\nRead a and b and print a+b.\nSample Input:\n2 3\nSample Output:\n5\n\n"
                     "Problem 2: Factorial\nPrint n!.\nSample Input:\n5\nSample Output:\n120\n", [1, 2], ["Sum of two numbers", "Factorial"]),
    "q_dot": ("Q1. Write a program to check whether a number is prime.\nQ2. Write a program to reverse a string.\nQ3. Find the largest element of an array.\n", [1, 2, 3], None),
    "q_dot_capital_and_colon": ("Q.1 Print the Fibonacci series up to n terms.\nQ.2 Check for a palindrome.\n", [1, 2], None),
    "question_word": ("Question 1) What is the output of the code below?\nint x = 5;\nQuestion 2) Write a function to swap two numbers.\n", [1, 2], None),
    "numbered_list": ("1. Write a C program to add two matrices.\n2. Write a C program to multiply two matrices.\n3. Write a C program to transpose a matrix.\n4. Write a program to find the trace.\n",
                      [1, 2, 3, 4], None),
    "numbered_paren": ("1) Implement a stack using an array.\n2) Implement a queue using two stacks.\n", [1, 2], None),
    "hindi_headers": ("प्रश्न 1: दो संख्याओं का योग निकालने का प्रोग्राम लिखिए।\nInput: 3 4\nOutput: 7\nप्रश्न 2: एक संख्या का फैक्टोरियल निकालिए।\nInput: 4\nOutput: 24\n", [1, 2], None),
    "hindi_devanagari_digits": ("प्रश्न १: पहला\nलिखिए\nप्रश्न २: दूसरा\n", [1, 2], None),
    "telugu_headers": ("ప్రశ్న 1: రెండు సంఖ్యల మొత్తం కనుగొనండి.\nSample Input: 2 3\nSample Output: 5\nప్రశ్న 2: ఫ్యాక్టోరియల్ కనుగొనండి.\n", [1, 2], None),
    "mixed_hindi_telugu_english": ("Problem 1: Sum\nप्रश्न 2: गुणा\nప్రశ్న 3: భాగహారం\n", [1, 2, 3], None),
    "table_pipes": ("| No | Problem |\n|----|---------|\n| 1 | Write a program to find the GCD of two numbers |\n| 2 | Write a program to find the LCM of two numbers |\n"
                    "| 3 | Write a program to check for an Armstrong number |\n", [1, 2, 3], None),
    "docx_table_cells": ("S.No | Problem Statement\n1 | Implement binary search on a sorted array\n2 | Implement bubble sort\n3 | Implement selection sort\n", [1, 2, 3], None),
    "preamble_and_q": ("Instructions: write every program in C. Use only standard input and output.\n\nQ1. Print the table of n.\nQ2. Count the vowels in a string.\n", [1, 2], None),
    "experiment_lab": ("Experiment 1: Implement linked list insertion\nAim: to insert nodes.\nExperiment 2: Implement linked list deletion\nAim: to delete nodes.\n", [1, 2], None),
    "markdown_headers": ("## Problem 1 - Two Sum\nGiven an array...\n\n## Problem 2 - Valid Parentheses\nGiven a string...\n", [1, 2], ["Two Sum", "Valid Parentheses"]),
    "exercise_with_subparts": ("Exercise 1: (a) Write a function to add. (b) Write a function to subtract.\nExercise 2: Write a recursive function for power.\n", [1, 2], None),
    "ocr_noise": ("Problem l: Sum of digits\nread n\n\nProblem 2: Count digits\nread n\n", [2], None),         # an OCR 'l' for 1: only the clear header is a header
    "single_problem_no_number": ("Write a program to read n numbers and print their average.\nInput: n then n integers.\nOutput: the average with two decimals.\n", [1], None),
    "list_inside_problem": ("Problem 1: Frequency count\nConstraints:\n1. n <= 100\n2. a[i] <= 1000\nProblem 2: Sorting\nConstraints:\n1. n <= 10\n", [1, 2], None),
}


@pytest.mark.parametrize("name", list(FIXTURES))
def test_splitting_fixture(name):
    text, numbers, titles = FIXTURES[name]
    s = split(text)
    assert [p.number for p in s.problems] == numbers, (name, s)
    if titles:
        assert [p.title for p in s.problems] == titles
    assert all(p.text.strip() for p in s.problems)


def test_there_are_at_least_twelve_fixtures():
    assert len(FIXTURES) >= 12


def test_hindi_and_telugu_headers_are_kept_as_written():
    s = split(FIXTURES["hindi_headers"][0])
    assert s.problems[0].text.startswith("प्रश्न 1") and "दो संख्याओं" in s.problems[0].title
    s = split(FIXTURES["telugu_headers"][0])
    assert s.problems[1].text.startswith("ప్రశ్న 2") and "ఫ్యాక్టోరియల్" in s.problems[1].text


def test_preamble_is_kept_as_common_instructions():
    s = split(FIXTURES["preamble_and_q"][0])
    assert "write every program in C" in s.common and len(s.problems) == 2


def test_a_long_preamble_is_not_common():
    s = split("intro " * 400 + "\nProblem 1: a\nx\nProblem 2: b\ny\n")
    assert s.common == "" and len(s.problems) == 2


def test_numbered_lists_inside_a_problem_do_not_split_it():
    s = split(FIXTURES["list_inside_problem"][0])
    assert len(s.problems) == 2 and "n <= 100" in s.problems[0].text


def test_the_whole_file_is_one_problem_when_nothing_matches():
    s = split("Given a graph, find the shortest path.\nMore text.")
    assert len(s.problems) == 1 and s.rule == "" and s.problems[0].number == 1


def test_empty_and_hostile_input():
    assert split("").problems == [] and split("   \n\n").problems == []
    assert len(split("Problem 1: " + "x" * 100000).problems) == 1
    assert len(split("\x00Problem 1: a\r\nProblem 2: b\r\n").problems) == 2


def test_a_picture_read_by_a_fake_ocr_splits_like_text():
    fake_ocr = lambda png: "Q1. Print Hello\n\nQ2. Add two numbers\nSample Input 1:\n2 3\nSample Output 1:\n5\n"      # noqa: E731
    s = split(fake_ocr(b"png"))
    assert [p.number for p in s.problems] == [1, 2]
    assert T.extract_samples(s.problems[1].text, R)[0].output == "5"


def test_a_broken_rule_in_the_data_file_is_skipped_not_fatal(tmp_path):
    cfg = {"numbering": [{"id": "bad", "rx": "(", "single": True}, {"id": "nonum", "rx": "^x$", "single": True},
                         {"id": "ok", "rx": r"^P(?P<num>\d+):\s*(?P<title>.*)$", "single": True}],
           "sample_markers": {"input": "("}}
    rules = T.load_rules(cfg)
    assert len(rules.problems) >= 2
    s = T.split_problems("P1: a\nP2: b", rules)
    assert [p.number for p in s.problems] == [1, 2]
    assert T.load_rules({}).numbering                                              # an empty file falls back to the built-in floor
    p = tmp_path / "p.json"
    p.write_text("{not json", encoding="utf-8")
    assert T.load_config(p) == {}


# ----------------------------------------------------------------------------- sample cases, verbatim
def test_sample_input_output_blocks():
    s = T.extract_samples("Sample Input 1:\n3\n1 2 3\nSample Output 1:\n6\n\nSample Input 2:\n2\n5 5\nSample Output 2:\n10\n", R)
    assert [(x.input, x.output) for x in s] == [("3\n1 2 3", "6"), ("2\n5 5", "10")]


def test_leetcode_style_examples():
    s = T.extract_samples("Example 1:\nInput: nums = [2,7,11,15], target = 9\nOutput: [0,1]\nExplanation: x\nExample 2:\nInput: nums = [3,2,4], target = 6\nOutput: [1,2]\n", R)
    assert [(x.input, x.output) for x in s] == [("nums = [2,7,11,15], target = 9", "[0,1]"), ("nums = [3,2,4], target = 6", "[1,2]")]


def test_same_line_form():
    s = T.extract_samples("Test case 1\nInput: 5\nOutput: 120\nTest case 2\nInput: 3\nOutput: 6\n", R)
    assert [(x.input, x.output) for x in s] == [("5", "120"), ("3", "6")]


def test_an_input_output_table():
    s = T.extract_samples("| Input | Output |\n|-------|--------|\n| 5 | 120 |\n| 3 | 6 |\n", R)
    assert [(x.input, x.output) for x in s] == [("5", "120"), ("3", "6")]


def test_a_format_description_is_not_a_sample():
    text = "Input Format: The first line contains an integer n.\nOutput Format: Print the sum of the numbers.\nSample Input:\n3\n1 2 3\nSample Output:\n6\n"
    assert [(x.input, x.output) for x in T.extract_samples(text, R)] == [("3\n1 2 3", "6")]
    assert T.extract_samples("Input: The first line contains the number of students and their names\nOutput: Print the highest scorer", R) == []


def test_unlabelled_data_like_pair_counts():
    s = T.extract_samples("Input:\n3 4\nOutput:\n7\n", R)
    assert [(x.input, x.output) for x in s] == [("3 4", "7")]


def test_hindi_statement_samples_are_verbatim():
    s = T.extract_samples(FIXTURES["hindi_headers"][0].split("प्रश्न 2")[0], R)
    assert [(x.input, x.output) for x in s] == [("3 4", "7")]


def test_fences_and_trailing_spaces_are_removed_but_content_is_verbatim():
    s = T.extract_samples("Sample Input:\n```\n  1 2  \n```\nSample Output:\n```\n3\n```\n", R)
    assert s[0].input == "  1 2" and s[0].output == "3"


def test_a_missing_output_is_not_a_sample_and_the_number_is_capped():
    assert T.extract_samples("Sample Input:\n5\n", R) == []
    many = "".join(f"Sample Input {i}:\n{i}\nSample Output {i}:\n{i * 2}\n" for i in range(1, 30))
    assert len(T.extract_samples(many, R)) == R.max_samples


def test_language_in_the_statement_and_slug():
    assert T.detect_language("Write a C++ program to sort", R) == "cpp"
    assert T.detect_language("Write a program in Java to add", R) == "java"
    assert T.detect_language("Using Python, print the table", R) == "python"
    assert T.detect_language("Write a C program to add", R) == "c"
    assert T.detect_language("Write a program to add", R) == ""
    assert T.detect_language("JavaScript function", R) == "javascript"
    assert T.slug("Two Sum [PERSON_1]!") == "two-sum" and T.slug("दो संख्याएँ") == "" and T.slug("A" * 100, 10) == "aaaaaaaaaa"
