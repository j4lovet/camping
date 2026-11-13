import unittest
from camping_priority import parse_ranges, expand_ranges


class RangeTests(unittest.TestCase):
    def test_boundaries_multiple_ranges_and_overlap_preserve_order(self):
        limits = {'A': (1, 9, 2), 'B': (1, 16, 2)}
        ranges = parse_ranges('b07 ~ b10, B09-B12 A1~A2', limits)
        self.assertEqual(ranges, ['B07~B10', 'B09~B12', 'A01~A02'])
        self.assertEqual(expand_ranges(ranges, limits),
                         ['B07', 'B08', 'B09', 'B10', 'B11', 'B12', 'A01', 'A02'])

    def test_invalid_ranges_rejected(self):
        limits = {'A': (1, 9, 2), 'B': (1, 16, 2)}
        for text in ('B10~B07', 'A01~B07', 'B00~B16', 'B01~B17', 'A01~A10', 'C01~C05', 'B07', ''):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_ranges(text, limits)

    def test_yeongok_bounds_and_optional_zone(self):
        limits = {'A': (101, 153, 3)}
        self.assertEqual(parse_ranges('130~145', limits), ['A130~A145'])
        self.assertEqual(expand_ranges(['A152~A153'], limits), ['A152', 'A153'])
        with self.assertRaises(ValueError):
            parse_ranges('A153~A154', limits)

    def test_invalid_settings_types_rejected(self):
        for value in ('B01~B07', [None], True, [1]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                expand_ranges(value, {'B': (1, 16, 2)})


if __name__ == '__main__':
    unittest.main()
