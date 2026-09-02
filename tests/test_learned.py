import tempfile
import unittest
from pathlib import Path

from app.learned import LearnedDictionary


class LearnedDictionaryTests(unittest.TestCase):
    def test_phrase_becomes_approved_after_two_matching_uses(self):
        with tempfile.TemporaryDirectory() as directory:
            dictionary = LearnedDictionary(Path(directory) / "learned.json")
            self.assertEqual(dictionary.record("  Уменьши до 100 Мб ", ("-100mb",), "compress"), "pending")
            self.assertIsNone(dictionary.lookup("уменьши до 100 мб"))
            self.assertEqual(dictionary.record("уменьши до 100 мб", ("-100mb",), "compress"), "approved")
            self.assertEqual(dictionary.lookup("УМЕНЬШИ   ДО 100 МБ")["commands"], ["-100mb"])

    def test_changed_plan_resets_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            dictionary = LearnedDictionary(Path(directory) / "learned.json")
            dictionary.record("сделай файл", ("-mp4",), "mp4")
            dictionary.record("сделай файл", ("-mp4",), "mp4")
            self.assertEqual(dictionary.record("сделай файл", ("-mov",), "mov"), "pending")
            self.assertIsNone(dictionary.lookup("сделай файл"))


if __name__ == "__main__":
    unittest.main()
