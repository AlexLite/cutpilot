import unittest

from app.rules import simple_plan


class RulePlanTests(unittest.TestCase):
    def test_simple_request_does_not_need_ai(self):
        self.assertEqual(simple_plan("сделай mp4 1080p без логотипа"), {
            "commands": ["-mp4", "-1080p", "-nologo"],
            "summary": "Локальный план без запроса к AI",
        })

    def test_concat_ranges_are_normalized_locally(self):
        plan = simple_plan("склей 0.15-0.50+1.25-2.13 и оставь лого")
        self.assertEqual(plan["commands"], ["-crp+0.15-0.50+1.25-2.13"])

    def test_collect_ranges_means_concatenate(self):
        plan = simple_plan("собери 0.16-0.25+1.20-1.25 пересчитай в mov и наложи лого")
        self.assertEqual(plan["commands"], ["-mov", "-nl", "-crp+0.16-0.25+1.20-1.25"])

    def test_complex_request_falls_back_to_ai(self):
        self.assertIsNone(simple_plan("сделай как для телеграма, но сохрани качество"))

    def test_edge_cut_uses_real_duration(self):
        plan = simple_plan("вырежи первые 15 секунд и в конце 10 секунд, сожми до 100 МБ", 181.04)
        self.assertEqual(plan["commands"], ["-crp-00.00-00.15+02.51-03.01", "-100mb"])

    def test_edge_cut_understands_cut_at_start_wording(self):
        plan = simple_plan("Обрезать вначале 10 секунд и 19 секунд в конце, сжать видео до 100 Mb", 181.04)
        self.assertEqual(plan["commands"], ["-crp-00.00-00.10+02.42-03.01", "-100mb"])

    def test_trim_by_timecode_is_local_and_keeps_explicit_logo(self):
        plan = simple_plan("Обрезать по таймкоду 1.25-1.55 и наложить лого")
        self.assertEqual(plan["commands"], ["-nl", "-crp-1.25-1.55"])

    def test_russian_file_size_unit_is_normalized(self):
        self.assertEqual(simple_plan("сожми до 100 Мегабайт")["commands"], ["-100mb"])

    def test_natural_compress_synonym_is_understood(self):
        self.assertEqual(simple_plan("уменьши до 100 Мб")["commands"], ["-100mb"])

    def test_reencode_to_mov_with_logo_is_understood(self):
        self.assertEqual(simple_plan("перекодируй в mov с лого")["commands"], ["-mov"])

    def test_latin_c_before_logo_means_keep_logo(self):
        self.assertEqual(simple_plan("перекодируй в mov c лого")["commands"], ["-mov"])


if __name__ == "__main__":
    unittest.main()
