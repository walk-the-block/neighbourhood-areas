"""Name selection for the areas file. Run: python -I -m unittest discover -s scripts/areas -p 'test_*.py'"""
import unittest

from names import has_letter, is_latin, pick_en, pick_lat, pick_local, pick_names


class Script(unittest.TestCase):
    def test_latin(self):
        for s in ('Exhibition', 'Lyon 7e Arrondissement', 'Søm', 'Rosser–Old Kildonan', 'Chūō-ku', 'Hawaiʻi Kai', '1ª Sección', 'Straße'):
            self.assertTrue(is_latin(s), s)

    def test_not_latin(self):
        for s in ('中野区', 'حي السكري', 'Москва', 'Αθήνα', '東池袋', 'Ōta 区', '12', '', None):
            self.assertFalse(is_latin(s), s)

    def test_letters(self):
        self.assertTrue(has_letter('Sector 7'))
        self.assertFalse(has_letter('12 - 3'))
        self.assertFalse(has_letter(None))


class English(unittest.TestCase):
    def test_common_en_first(self):
        self.assertEqual(pick_en('حي السكري', {'en': 'As-Sukkari', 'ku': 'x'}), 'As-Sukkari')
        self.assertEqual(pick_en('Hanano', {'en': 'Hanano district'}), 'Hanano district')

    def test_latin_primary(self):
        self.assertEqual(pick_en('Exhibition'), 'Exhibition')
        self.assertEqual(pick_en('Lyon 7e Arrondissement', {'fr': 'Lyon 7e'}), 'Lyon 7e Arrondissement')

    def test_en_that_is_not_latin_is_skipped(self):
        self.assertEqual(pick_en('Exhibition', {'en': 'エキシビション'}), 'Exhibition')

    def test_english_rule_before_romanisation(self):
        rules = [{'variant': 'alternate', 'language': 'en', 'value': 'Old Town'},
                 {'variant': 'official', 'language': 'en', 'value': 'Old City'}]
        self.assertEqual(pick_en('Старый город', {'ru-Latn': 'Staryy gorod'}, rules), 'Old City')

    def test_romanisation(self):
        self.assertEqual(pick_en('上高田', {'ja': '上高田', 'ja-Hira': 'かみたかだ', 'ja-Latn': 'Kami-Takada'}), 'Kami-Takada')
        self.assertEqual(pick_en('海淀', {'zh-Latn-wadegile': 'Hai-tien', 'zh-Latn-pinyin': 'Haidian'}), 'Haidian')
        self.assertEqual(pick_en('海淀', {'zh-Latn-pinyin': 'Haidian', 'zh-Latn': 'Haidian Qu'}), 'Haidian Qu')

    def test_romanisation_from_rules(self):
        self.assertEqual(pick_en('中区', {}, [{'variant': 'alternate', 'language': 'ja-Latn', 'value': 'Naka-ku'}]), 'Naka-ku')

    def test_no_english_no_romanisation(self):
        self.assertIsNone(pick_en('Арбат', {'de': 'Arbat'}))
        self.assertIsNone(pick_en('Арбат', {'ja-Hira': 'あるばと'}))


class Local(unittest.TestCase):
    def test_local(self):
        self.assertEqual(pick_local(' 中野区 '), '中野区')
        self.assertIsNone(pick_local('Exhibition'))
        self.assertIsNone(pick_local('42'))

    def test_lat(self):
        self.assertEqual(pick_lat(' München '), 'München')
        self.assertIsNone(pick_lat('中野区'))
        self.assertIsNone(pick_lat('42'))

    def test_all_three(self):
        self.assertEqual(pick_names('中野区', {'en': 'Nakano'}), ('Nakano', None, '中野区'))
        self.assertEqual(pick_names('Exhibition'), ('Exhibition', 'Exhibition', None))
        # Its own Latin name stays, beside the English one: the old path called it Innere Stadt.
        self.assertEqual(pick_names('Innere Stadt', {'en': 'Inner City'}), ('Inner City', 'Innere Stadt', None))
        self.assertEqual(pick_names('Арбат'), (None, None, 'Арбат'))
        self.assertEqual(pick_names('12'), (None, None, None))


if __name__ == '__main__':
    unittest.main()
