# -*- coding: utf-8 -*-
"""Тесты, которые не ходят в сеть.

Проверяют то, что решается кодом, а не моделью: разбор ответа, сборку
промпта, отсев мёртвых чатов, защиту фактов в тексте приглашения.

    python -m unittest discover -s tests -v

Качество самой классификации проверяется отдельно, на живых переписках:
    python tools/eval_types.py run
"""
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import names, openrouter, priority, progress, reactions   # noqa: E402
from app import softeners, tgcontacts                       # noqa: E402


class TestAnswerParsing(unittest.TestCase):
    def test_json_in_fence(self):
        answer = '```json\n{"type": "клиент", "why": "смета"}\n```'
        self.assertEqual(openrouter.parse_json(answer)['type'], 'клиент')

    def test_json_with_prose_around(self):
        answer = 'Вот ответ: {"type": "коллеги"} — надеюсь, помог'
        self.assertEqual(openrouter.parse_json(answer)['type'], 'коллеги')

    def test_broken_json(self):
        self.assertEqual(openrouter.parse_json('не json вовсе'), {})

    def test_normalize_known_kinds(self):
        for kind in openrouter.KINDS:
            self.assertEqual(openrouter.normalize(kind), kind)
        self.assertEqual(openrouter.normalize('"Клиент".'), 'клиент')
        self.assertEqual(openrouter.normalize('чепуха'), 'другое')

    def test_normalize_old_and_loose_names(self):
        # старые названия типов из прежних версий
        self.assertEqual(openrouter.normalize('ит-компания'), 'ит и digital')
        self.assertEqual(openrouter.normalize('сотрудник'), 'коллеги')
        # и вольные формулировки модели
        self.assertEqual(openrouter.normalize('кандидат на вакансию'), 'соискатель')
        self.assertEqual(openrouter.normalize('фрилансер'), 'подрядчик')
        self.assertEqual(openrouter.normalize('digital-агентство'), 'ит и digital')

    def test_name_must_be_cyrillic_and_clean(self):
        self.assertEqual(openrouter._clean_name('иван'), 'Иван')
        self.assertEqual(openrouter._clean_name('Иван Петров'), 'Иван')
        self.assertEqual(openrouter._clean_name('John'), 'Джон')
        self.assertEqual(openrouter._clean_name('Иван[Alto]'), '')
        self.assertEqual(openrouter._clean_name(''), '')


class TestPromptAssembly(unittest.TestCase):
    """Промпт собирается из кусков — проверяем, что ничего не отвалилось."""

    def test_person_prompt_formats(self):
        text = openrouter.SYSTEM_PERSON.format(
            about='О владельце.', company=openrouter.company_block('Alto'),
            examples=openrouter.examples_block([('Кто-то', 'коллеги')]))
        self.assertIn('О владельце.', text)
        self.assertIn('Alto', text)
        self.assertIn('Кто-то -> коллеги', text)
        # категория выбирается после who и why — иначе модель гадает сразу
        self.assertLess(text.index('"who"'), text.index('"type"'))
        self.assertLess(text.index('"why"'), text.index('"type"'))
        for kind in openrouter.KINDS:
            self.assertIn(kind, text)

    def test_group_prompt_formats(self):
        text = openrouter.SYSTEM_GROUP.format(about='О владельце.')
        for fit in openrouter.FITS:
            self.assertIn(fit, text)

    def test_company_block_empty_when_not_set(self):
        self.assertEqual(openrouter.company_block(''), '')
        self.assertEqual(openrouter.company_block('  ,  '), '')

    def test_company_block_lists_names(self):
        block = openrouter.company_block('Alto, alto.codes')
        self.assertIn('Alto, alto.codes', block)

    def test_examples_block_empty(self):
        self.assertEqual(openrouter.examples_block([]), '')
        self.assertEqual(openrouter.examples_block([('', 'клиент')]), '')

    def test_examples_keep_order(self):
        block = openrouter.examples_block([('Первый', 'клиент'),
                                           ('Второй', 'коллеги')])
        self.assertLess(block.index('Первый'), block.index('Второй'))

    def test_examples_one_kind_does_not_eat_the_list(self):
        many = [('Компания {}'.format(i), 'ит-компания') for i in range(20)]
        many.append(('Заказчик', 'клиент'))
        block = openrouter.examples_block(many)
        self.assertIn('Заказчик -> клиент', block)
        self.assertLessEqual(block.count('ит-компания'), 4)

    def test_examples_respect_limit(self):
        pairs = [('Имя {}'.format(i), kind)
                 for i, kind in enumerate(openrouter.KINDS * 5)]
        block = openrouter.examples_block(pairs, limit=6)
        self.assertEqual(block.count('\n- '), 6)


class TestDeadChats(unittest.TestCase):
    def test_record_without_name_and_username_is_dead(self):
        self.assertTrue(tgcontacts.is_dead_record({'title': 'без имени', 'username': ''}))
        self.assertTrue(tgcontacts.is_dead_record({'title': '', 'username': ''}))

    def test_record_with_username_survives(self):
        self.assertFalse(tgcontacts.is_dead_record({'title': '', 'username': 'ivan'}))
        self.assertFalse(tgcontacts.is_dead_record({'title': 'Иван', 'username': ''}))

    def test_entity_checks(self):
        class Fake(object):
            def __init__(self, **kw):
                self.__dict__.update(kw)

        self.assertTrue(tgcontacts.is_dead(Fake(deleted=True)))
        self.assertTrue(tgcontacts.is_service(Fake(bot=True)))
        self.assertTrue(tgcontacts.is_service(Fake(id=777000)))
        self.assertTrue(tgcontacts.is_obsolete(Fake(migrated_to=object())))
        self.assertFalse(tgcontacts.is_dead(Fake(deleted=False)))

    def test_store_drops_dead_and_renames_old_kind(self):
        rows = {
            '1': {'id': '1', 'title': 'без имени', 'username': '', 'kind': 'человек'},
            '2': {'id': '2', 'title': 'Иван', 'username': '', 'kind': 'человек',
                  'type': 'сотрудник', 'why': 'выставлено вручную'},
            '3': {'id': '3', 'title': 'Пётр', 'username': 'p', 'kind': 'человек',
                  'type': 'ит-компания'},
        }
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'contacts.json'
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(rows, f, ensure_ascii=False)
            original = tgcontacts.STORE_PATH
            tgcontacts.STORE_PATH = path
            try:
                store = tgcontacts.load_store()
            finally:
                tgcontacts.STORE_PATH = original

        self.assertNotIn('1', store)                      # мёртвый убран
        self.assertEqual(store['2']['type'], 'коллеги')   # переименован
        self.assertEqual(store['2']['by'], 'человек')     # правка узнана
        self.assertEqual(store['3']['type'], 'ит и digital')

    def test_links_for_telegram(self):
        self.assertEqual(tgcontacts.tg_link({'username': 'ivan'}),
                         'tg://resolve?domain=ivan')
        self.assertEqual(tgcontacts.tg_link({'username': '', 'id': '12345',
                                             'kind': tgcontacts.PERSON}),
                         'tg://openmessage?user_id=12345')
        self.assertEqual(tgcontacts.tg_link({'username': '', 'id': '-1001234567',
                                             'kind': tgcontacts.GROUP}),
                         'tg://openmessage?chat_id=1234567')


class TestInviteText(unittest.TestCase):
    """Заготовку модель переписывать не должна — это проверяется кодом."""

    def test_greeting_removed_without_losing_words(self):
        self.assertEqual(openrouter.strip_greeting('Привет, мы проводим встречу'),
                         'мы проводим встречу')
        self.assertEqual(openrouter.strip_greeting('Привет, Иван! Мы проводим'),
                         'Мы проводим')

    def test_filler_sentences_cut(self):
        opening = 'Иван, привет! Рад, что мы на связи. Как дела?'
        self.assertEqual(openrouter.clean_opening(opening), 'Иван, привет!')

    def test_opening_without_filler_kept(self):
        opening = 'Иван, привет! Извини, что пропустил вопрос.'
        self.assertEqual(openrouter.clean_opening(opening), opening)

    def test_anchors_catch_facts(self):
        found = openrouter.anchors('12 августа, промокод UMCBIZ, https://a.b')
        self.assertIn('12', found)
        self.assertIn('umcbiz', found)
        self.assertIn('https://a.b', found)

    def test_already_invited_reads_own_lines(self):
        transcript = 'он: привет\nя: зову тебя на конференцию'
        self.assertTrue(openrouter.already_invited('AGDays', transcript))

    def test_not_invited_when_only_they_wrote(self):
        transcript = 'он: у нас тут конференция была'
        self.assertFalse(openrouter.already_invited('AGDays', transcript))


if __name__ == '__main__':
    unittest.main()


class TestCompanyAndRole(unittest.TestCase):
    """Компанию и должность модель пишет как попало — приводим к виду."""

    def test_roles_to_typical_names(self):
        cases = {
            'CEO': 'CEO', 'основатель и генеральный директор': 'CEO',
            'Технический директор': 'CTO', 'ИТ-директор': 'ИТ-директор',
            'директор по маркетингу': 'маркетинг',
            'коммерческий директор': 'продажи',
            'Product Owner': 'продакт', 'проджект-менеджер': 'проджект',
            'ведущий дизайнер': 'дизайнер', 'backend-разработчик': 'разработчик',
            'HR-менеджер': 'HR', 'начальник отдела': 'начальник',
        }
        for text, want in cases.items():
            self.assertEqual(openrouter.normalize_role(text), want, text)

    def test_qualifier_is_not_dropped(self):
        # «креативный директор» — не «директор»: уточнение и есть должность
        self.assertEqual(openrouter.normalize_role('креативный директор'),
                         'креативный директор')
        self.assertEqual(openrouter.normalize_role('арт-директор'), 'арт-директор')
        self.assertEqual(openrouter.normalize_role('ведущий специалист'),
                         'ведущий специалист')
        # а голое слово упрощаем, в том числе с пустым уточнением
        self.assertEqual(openrouter.normalize_role('Директор'), 'директор')
        self.assertEqual(openrouter.normalize_role('директор компании'), 'директор')
        self.assertEqual(openrouter.normalize_role('менеджер'), 'менеджер')
        # известные должности по-прежнему сводятся к типовым
        self.assertEqual(openrouter.normalize_role('генеральный директор'), 'CEO')
        self.assertEqual(openrouter.normalize_role('менеджер проектов'), 'проджект')

    def test_callsign_is_not_a_role(self):
        # в профилях попадаются позывные и коды — в должность им нельзя
        self.assertEqual(openrouter.normalize_role('R8AEC'), '')
        self.assertEqual(openrouter.normalize_role('XYZ'), '')
        self.assertEqual(openrouter.normalize_role('топ-1'), '')

    def test_unknown_role_kept_short(self):
        self.assertEqual(openrouter.normalize_role('шеф-повар'), 'шеф-повар')
        self.assertEqual(openrouter.normalize_role('неизвестно'), '')
        self.assertEqual(openrouter.normalize_role(''), '')

    def test_company_cleaned(self):
        self.assertEqual(openrouter.normalize_company('ООО «Альфа»'), 'Альфа')
        self.assertEqual(openrouter.normalize_company('  Сбер '), 'Сбер')
        self.assertEqual(openrouter.normalize_company('компания неизвестна'), '')
        self.assertEqual(openrouter.normalize_company('не указана'), '')

    def test_role_written_into_company_is_dropped(self):
        # частая ошибка модели: в компанию попадает должность
        self.assertEqual(openrouter.normalize_company('дизайнер'), '')
        self.assertEqual(openrouter.normalize_company('фрилансер'), '')
        self.assertEqual(openrouter.normalize_company('разработчик'), '')
        # а настоящее название с тем же словом остаётся
        self.assertEqual(openrouter.normalize_company('Дизайнер Групп 24'),
                         'Дизайнер Групп 24')


class TestGreetingName(unittest.TestCase):
    """С чего начнётся сообщение — «Привет, Иван!» или просто «Привет!»."""

    def test_model_name_wins(self):
        self.assertEqual(tgcontacts.greeting_name(
            {'title': 'Ekaterina Москвичева[ВашЗаказ]', 'name': 'Екатерина'}),
            'Екатерина')

    def test_name_from_title_without_tags(self):
        self.assertEqual(tgcontacts.greeting_name(
            {'title': 'Даниил Ильин[Alto][дизайнер]'}), 'Даниил')
        self.assertEqual(tgcontacts.greeting_name(
            {'title': 'Никита [метран]'}), 'Никита')

    def test_latin_name_is_translated(self):
        # «Привет, Sergey Чернобровкин[KTS]!» писать нельзя, а «Сергей» — можно
        self.assertEqual(tgcontacts.greeting_name(
            {'title': 'Sergey Чернобровкин[KTS]'}), 'Сергей')
        self.assertEqual(tgcontacts.greeting_name(
            {'title': 'Roman Teterin[PM]'}), 'Роман')
        self.assertEqual(tgcontacts.greeting_name({'title': ''}), '')


class TestTranslit(unittest.TestCase):
    """«Maxim Kolmogorov» — это Максим, а не повод остаться без имени."""

    def test_common_names(self):
        cases = {'Maxim': 'Максим', 'Sergey': 'Сергей', 'Ivan': 'Иван',
                 'Alexey': 'Алексей', 'Dmitriy': 'Дмитрий', 'Mikhail': 'Михаил',
                 'Ekaterina': 'Екатерина', 'Anastasia': 'Анастасия',
                 'Artem': 'Артём', 'Yuri': 'Юрий', 'Kate': 'Катя'}
        for latin, cyrillic in cases.items():
            self.assertEqual(names.to_cyrillic(latin), cyrillic, latin)

    def test_not_a_name(self):
        self.assertEqual(names.to_cyrillic('x9'), '')
        self.assertEqual(names.to_cyrillic(''), '')
        self.assertEqual(names.to_cyrillic('Иван'), '')      # уже кириллица

    def test_used_for_greeting_and_model_answer(self):
        self.assertEqual(tgcontacts.greeting_name(
            {'title': 'Maxim Kolmogorov'}), 'Максим')
        self.assertEqual(openrouter._clean_name('Maxim'), 'Максим')


class TestCompanyFromTitle(unittest.TestCase):
    """Компанию часто пишут прямо в имени чата, а модель её пропускает."""

    def test_trailing_lowercase_word(self):
        self.assertEqual(openrouter.title_company('Дмитрий Щипачев finch'), 'finch')
        self.assertEqual(openrouter.title_company('Даниил Дмитриевич ocman-digital.ru'),
                         'ocman-digital.ru')

    def test_brackets(self):
        self.assertEqual(openrouter.title_company('Даниил Ильин[Alto][дизайнер]'), 'Alto')
        self.assertEqual(openrouter.title_company('Никита [метран]'), 'метран')
        self.assertEqual(openrouter.title_company('Александр [Зеленая Мойка]'),
                         'Зеленая Мойка')

    def test_surname_is_not_a_company(self):
        self.assertEqual(openrouter.title_company('Дмитрий Тарасов'), '')
        self.assertEqual(openrouter.title_company('Папа'), '')
        self.assertEqual(openrouter.title_company('Алексей Макаров С Бойцовского'), '')

    def test_role_in_brackets_is_not_a_company(self):
        self.assertEqual(openrouter.title_company('Michael Bogdanov[ИТ-директор]'), '')

    def test_known_role_only_for_real_roles(self):
        self.assertEqual(openrouter.known_role('дизайнер'), 'дизайнер')
        self.assertEqual(openrouter.known_role('finch'), '')
        self.assertEqual(openrouter.known_role('Alto'), '')


class TestProgressUndo(unittest.TestCase):
    """Отметку «уже писали» надо уметь снимать — и по одному, и разом."""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.original = progress.STATE_DIR
        progress.STATE_DIR = Path(self.folder.name)

    def tearDown(self):
        progress.STATE_DIR = self.original
        self.folder.cleanup()

    def test_unmark_one(self):
        progress.mark_done('папка', 'ivan')
        progress.mark_done('папка', 'petr')
        self.assertEqual(progress.load_done('папка'), {'ivan', 'petr'})

        self.assertTrue(progress.unmark_done('папка', 'IVAN'))
        self.assertEqual(progress.load_done('папка'), {'petr'})

    def test_unmark_missing_is_harmless(self):
        progress.mark_done('папка', 'ivan')
        progress.unmark_done('папка', 'нет-такого')
        self.assertEqual(progress.load_done('папка'), {'ivan'})

    def test_reset_all(self):
        progress.mark_done('папка', 'ivan')
        self.assertTrue(progress.reset_done('папка'))
        self.assertEqual(progress.load_done('папка'), set())


class TestInviteWindow(unittest.TestCase):
    """Конференция ежегодная: приглашение годовой давности — не повод молчать."""

    BASE = 'Проводим AI Growth Day (ex AGDAYs). Программа и билеты — agday.ru'

    def test_keys_are_distinctive(self):
        keys = openrouter.invite_keys(self.BASE)
        self.assertIn('agday.ru', keys)
        self.assertIn('agdays', keys)
        # общие слова в приметы не годятся: они есть в любом разговоре про конфы
        self.assertNotIn('конференция', keys)
        self.assertNotIn('программа', keys)

    def test_mentions(self):
        keys = openrouter.invite_keys(self.BASE)
        self.assertTrue(openrouter.mentions_invite('вот-вот AGDays, приходи', keys))
        self.assertFalse(openrouter.mentions_invite('зову на митап по битриксу', keys))

    def test_transcript_check_ignores_their_words(self):
        # приметы в ЕГО сообщении не значат, что звали мы
        self.assertFalse(openrouter.invited_to_this(
            self.BASE, 'он: был на AGDays в прошлом году'))
        self.assertTrue(openrouter.invited_to_this(
            self.BASE, 'я: зову на AGDays\nон: подумаю'))


class TestNameOrder(unittest.TestCase):
    """В Telegram подписываются и «Имя Фамилия», и «Фамилия Имя»."""

    def test_surname_first(self):
        self.assertEqual(tgcontacts.greeting_name({'title': 'Ковригина Юлия'}), 'Юлия')
        self.assertEqual(tgcontacts.greeting_name({'title': 'Конаков Артём, UXART'}),
                         'Артём')

    def test_name_first_still_works(self):
        # «Екатерина» кончается так же, как фамилия «Ковригина» —
        # по окончаниям различить нельзя, помогает только словарь имён
        self.assertEqual(tgcontacts.greeting_name({'title': 'Екатерина Зеленская[ЦИТ]'}),
                         'Екатерина')
        self.assertEqual(tgcontacts.greeting_name({'title': 'Даниил Ильин[Alto]'}),
                         'Даниил')

    def test_latin_in_any_order(self):
        self.assertEqual(tgcontacts.greeting_name({'title': 'Kovrigina Yulia'}), 'Юлия')
        self.assertEqual(tgcontacts.greeting_name({'title': 'Maxim Kolmogorov'}),
                         'Максим')

    def test_unknown_name_keeps_first_word(self):
        self.assertEqual(tgcontacts.greeting_name({'title': 'Бабруев | Ветеран'}),
                         'Бабруев')


class TestTranslitFixes(unittest.TestCase):
    """Ошибки, найденные на живой базе контактов."""

    def test_endings(self):
        cases = {'Aleksei': 'Алексей', 'Sergei': 'Сергей', 'Matvei': 'Матвей',
                 'Vitalii': 'Виталий', 'Dmitrii': 'Дмитрий',
                 'Anastasiia': 'Анастасия', 'Mariia': 'Мария',
                 'Victoria': 'Виктория', 'Valeriia': 'Валерия'}
        for latin, cyrillic in cases.items():
            self.assertEqual(names.to_cyrillic(latin), cyrillic, latin)

    def test_endings_do_not_break_short_names(self):
        # правило про окончания не должно портить обычные имена
        self.assertEqual(names.to_cyrillic('Diana'), 'Диана')
        self.assertEqual(names.to_cyrillic('Alina'), 'Алина')
        self.assertEqual(names.to_cyrillic('Ivan'), 'Иван')

    def test_digraphs(self):
        self.assertEqual(names.to_cyrillic('Arthur'), 'Артур')
        self.assertEqual(names.to_cyrillic('Christina'), 'Кристина')
        self.assertEqual(names.to_cyrillic('Chris'), 'Крис')

    def test_acronyms_are_not_names(self):
        for word in ('GGRN', 'AD', 'KSK', 'VLG', 'TNG'):
            self.assertEqual(names.to_cyrillic(word), '', word)

    def test_companies_get_no_greeting(self):
        for title in ('Deli Bag', 'Brainics Support', 'Amex Development',
                      'Gorizont Entertainment', 'Best Service Assistance'):
            self.assertEqual(tgcontacts.greeting_name({'title': title}), '', title)

    def test_honorific_skipped(self):
        self.assertEqual(tgcontacts.greeting_name({'title': 'Dr Nadya'}), 'Надя')


class TestReadFailureIsNotEmpty(unittest.TestCase):
    """Ошибка чтения переписки и пустой чат — разные вещи."""

    def test_nothing_to_read_only_for_real_emptiness(self):
        self.assertTrue(openrouter.nothing_to_read('', ''))
        self.assertTrue(openrouter.nothing_to_read('он: ок', ''))
        self.assertFalse(openrouter.nothing_to_read('', 'CEO ИТ-компании'))
        long_talk = 'он: ' + 'а' * 200
        self.assertFalse(openrouter.nothing_to_read(long_talk, ''))

    def test_transcript_formatting_from_rows(self):
        rows = [(True, None, 'второе'), (False, None, 'первое')]
        self.assertEqual(tgcontacts.as_transcript(rows), 'он: первое\nя: второе')
        self.assertEqual(tgcontacts.as_transcript([]), '')


class TestFloodGuard(unittest.TestCase):
    """Если Telegram просит подождать — ждут все читатели сразу."""

    def test_short_wait_is_waited_out(self):
        import asyncio
        from telethon.errors.rpcerrorlist import FloodWaitError

        calls = {'n': 0}

        async def action():
            calls['n'] += 1
            if calls['n'] == 1:
                raise FloodWaitError(request=None)
            return 'готово'

        async def run():
            guard = tgcontacts.FloodGuard()
            guard.limit = 300
            # подменяем сон, чтобы тест не ждал по-настоящему
            original = asyncio.sleep
            asyncio.sleep = lambda _s: original(0)
            try:
                return await tgcontacts.call_slowly(action, guard)
            finally:
                asyncio.sleep = original

        self.assertEqual(asyncio.run(run()), 'готово')
        self.assertEqual(calls['n'], 2)

    def test_long_wait_stops_the_run(self):
        import asyncio
        from telethon.errors.rpcerrorlist import FloodWaitError

        async def action():
            error = FloodWaitError(request=None)
            error.seconds = 3600
            raise error

        async def run():
            guard = tgcontacts.FloodGuard(limit=300)
            result = await tgcontacts.call_slowly(action, guard)
            return result, guard.stopped

        result, stopped = asyncio.run(run())
        self.assertIsNone(result)
        self.assertIn('лимит', stopped)

    def test_other_errors_are_not_retried_forever(self):
        import asyncio

        calls = {'n': 0}

        async def action():
            calls['n'] += 1
            raise ValueError('нет такой сущности')

        self.assertIsNone(asyncio.run(tgcontacts.call_slowly(action, None)))
        self.assertEqual(calls['n'], 1)


class TestReactions(unittest.TestCase):
    """Память о прошлом ответе. Виды взяты из 92 реальных ответов."""

    def test_recognises_far_away(self):
        self.assertEqual(
            reactions.classify('Спасибо за приглашение, но я не езжу по другим городам'),
            'далеко')

    def test_recognises_busy_dates(self):
        self.assertEqual(
            reactions.classify('в этот период каникулы и уже запланирована поездка'),
            'занят')

    def test_recognises_passing_to_colleagues(self):
        self.assertEqual(reactions.classify('ок, скинул нашему лиду тимлидов'),
                         'передаст')

    def test_plain_thanks_says_nothing(self):
        self.assertEqual(reactions.classify('Спасибо! 👍'), '')
        self.assertEqual(reactions.classify(''), '')

    def test_reply_taken_only_after_our_invite(self):
        talk = ('он: далеко ехать, не поеду\n'
                'я: как дела?\n'
                'он: нормально')
        # про конференцию речи не было — вспоминать нечего
        self.assertEqual(reactions.from_transcript(talk), '')

        invite = ('я: зову на AI Growth Days 28 августа\n'
                  'он: спасибо, но я не езжу по другим городам')
        self.assertEqual(reactions.from_transcript(invite), 'далеко')

    def test_note_mentions_memory(self):
        self.assertIn('далеко', reactions.note('далеко', 'ты'))
        self.assertIn('вам', reactions.note('далеко', 'вы'))
        self.assertEqual(reactions.note('спросил', 'ты'), '')

    def test_strongest_reaction_wins(self):
        self.assertEqual(reactions.best(['подумает', 'далеко', 'придёт']), 'далеко')
        self.assertEqual(reactions.best(['', '']), '')


class TestMemoryInGreeting(unittest.TestCase):

    def test_memory_replaces_generic_apology(self):
        base = 'Привет, {NAME}! Мы проводим AI Growth Days 28 августа.'
        history = ('я: зову на AI Growth Days\n'
                   'он: спасибо, но мне далеко ехать')
        text, note = softeners.build(base, name='Иван', address='ты',
                                     transcript=history)
        self.assertIn('Помню, что тебе далеко', text)
        self.assertNotIn('опять только с конфой', text)
        self.assertIn('далеко', note)

    def test_group_talk_used_when_dialog_is_silent(self):
        base = 'Привет, {NAME}! Зовём на конференцию.'
        text, _note = softeners.build(
            base, name='Иван', address='ты', transcript='',
            talk=['были на AGDays в прошлом году, лучшая конфа'])
        self.assertIn('в прошлый раз', text.lower())

    def test_body_is_untouched(self):
        base = 'Привет, {NAME}! Промокод на 11%, 28 августа.'
        history = 'я: зову на AGDays\nон: не езжу по другим городам'
        text, _note = softeners.build(base, name='Иван', address='ты',
                                      transcript=history)
        self.assertIn('Промокод на 11%, 28 августа.', text)


class TestRegister(unittest.TestCase):
    """Регистр общения: считается из обращения и типа, без модели."""

    def test_register_from_address_and_kind(self):
        self.assertEqual(softeners.register('ты', 'близкие'), 'свои')
        self.assertEqual(softeners.register('ты', 'коллеги'), 'свои')
        self.assertEqual(softeners.register('ты', 'подрядчик'), 'рабочий')
        self.assertEqual(softeners.register('вы', 'близкие'), 'формальный')
        self.assertEqual(softeners.register('', ''), 'формальный')

    def test_formal_greeting_is_not_privet(self):
        text, note = softeners.build('Зовём на конференцию.', name='Иван',
                                     address='вы', kind='клиент')
        self.assertTrue(text.startswith('Иван, добрый день!'))
        self.assertIn('формальный', note)

    def test_friendly_greeting(self):
        text, _ = softeners.build('Зовём на конференцию.', name='Иван',
                                  address='ты', kind='близкие')
        self.assertTrue(text.startswith('Иван, привет!'))

    def test_no_emoji_and_no_sorry_for_clients(self):
        history = 'я: зову на конференцию AGDays'
        text, _ = softeners.build('Зовём на конференцию.', name='Иван',
                                  address='вы', kind='клиент',
                                  transcript=history)
        self.assertIn('Пишу вам снова про конференцию', text)
        self.assertNotIn('сорри', text)
        self.assertNotIn('🙂', text)

    def test_close_contact_keeps_the_smiley(self):
        history = 'я: зову на конференцию AGDays'
        text, _ = softeners.build('Зовём на конференцию.', name='Иван',
                                  address='ты', kind='близкие',
                                  transcript=history)
        self.assertIn('сорри 🙂', text)

    def test_work_ty_is_between_the_two(self):
        history = 'я: зову на конференцию AGDays'
        text, _ = softeners.build('Зовём на конференцию.', name='Иван',
                                  address='ты', kind='подрядчик',
                                  transcript=history)
        self.assertIn('опять только с конфой 🙂', text)
        self.assertNotIn('сорри', text)

    def test_apology_for_missed_message_matches_register(self):
        history = 'я: привет\nон: а можно подробнее?'
        formal, _ = softeners.build('Текст.', name='Иван', address='вы',
                                    kind='клиент', transcript=history)
        close, _ = softeners.build('Текст.', name='Иван', address='ты',
                                   kind='близкие', transcript=history)
        self.assertIn('извините.', formal)
        self.assertNotIn('🙈', formal)
        self.assertIn('🙈', close)


class TestApologyOnlyWhenAsked(unittest.TestCase):
    """Извинение за пропуск — только если сообщение ждало ответа."""

    def test_question_needs_reply(self):
        self.assertTrue(softeners.needs_reply('А можно программу посмотреть?'))
        self.assertTrue(softeners.needs_reply('подскажи, когда удобно созвониться'))

    def test_thanks_does_not(self):
        for text in ('Спасибо!', 'Спасибо 👍', 'ок', 'Ага, понял', 'Отлично)',
                     '👍', 'Договорились'):
            self.assertFalse(softeners.needs_reply(text), text)

    def test_long_message_counts(self):
        long_text = 'Мы у себя внедрили ИИ в код-ревью и в тесты. ' * 6
        self.assertTrue(softeners.needs_reply(long_text))

    def test_no_apology_after_thanks(self):
        text, note = softeners.build('Зовём на конференцию.', name='Иван',
                                     address='ты', kind='близкие',
                                     transcript='я: держи ссылку\nон: Спасибо! 👍')
        self.assertNotIn('пропустил', text.lower())
        self.assertNotIn('извин', note)

    def test_apology_after_a_question(self):
        text, _ = softeners.build('Зовём на конференцию.', name='Иван',
                                  address='ты', kind='близкие',
                                  transcript='я: привет\nон: а сколько стоит участие?')
        self.assertIn('пропустил', text.lower())


class TestLongSilenceBridge(unittest.TestCase):
    """Формальный контакт после долгого молчания: сначала напоминание."""

    TODAY = date(2026, 8, 14)
    # мостик ставится только там, где было что прерывать
    TALK = ('я: добрый день, посмотрели смету\nон: да, вопросы есть\n'
            'я: давайте созвонимся\nон: удобно в четверг\n'
            'я: ок, поставил\nон: спасибо')

    def test_when_phrase(self):
        self.assertEqual(softeners.when_phrase('2026-03-04', self.TODAY),
                         'в марте')
        self.assertEqual(softeners.when_phrase('2025-10-31', self.TODAY),
                         'в октябре прошлого года')
        self.assertEqual(softeners.when_phrase('2023-05-01', self.TODAY),
                         'в 2023 году')
        self.assertEqual(softeners.when_phrase('', self.TODAY), '')

    def test_silence_counted_in_months(self):
        self.assertEqual(softeners.silent_months('2026-08-01', self.TODAY), 0)
        self.assertEqual(softeners.silent_months('2025-10-31', self.TODAY), 10)
        self.assertIsNone(softeners.silent_months('не дата', self.TODAY))

    def test_bridge_added_for_silent_formal(self):
        text, note = softeners.build(
            'Зовём на конференцию.', name='Иван', address='вы', kind='клиент',
            last_seen='2025-10-31', recall='интеграцию с 1С',
            transcript=self.TALK, today=self.TODAY)
        self.assertIn('Мы общались в октябре прошлого года про интеграцию с 1С',
                      text)
        self.assertIn('напомнил о прошлом разговоре', note)

    def test_bridge_works_without_recall(self):
        text, _ = softeners.build(
            'Зовём на конференцию.', name='Иван', address='вы', kind='клиент',
            last_seen='2025-10-31', transcript=self.TALK, today=self.TODAY)
        self.assertIn('Мы общались в октябре прошлого года.', text)
        self.assertNotIn('не пересекались', text)

    def test_no_bridge_for_fresh_dialog(self):
        text, _ = softeners.build(
            'Зовём на конференцию.', name='Иван', address='вы', kind='клиент',
            last_seen='2026-07-01', recall='интеграцию с 1С', today=self.TODAY)
        self.assertNotIn('Мы общались', text)

    def test_no_bridge_for_close_contacts(self):
        text, _ = softeners.build(
            'Зовём на конференцию.', name='Иван', address='ты', kind='близкие',
            last_seen='2024-01-01', recall='переезд', today=self.TODAY)
        self.assertNotIn('Мы общались', text)

    def test_bridge_mentions_previous_invite(self):
        text, _ = softeners.build(
            'Зовём на конференцию AGDays.', name='Иван', address='вы',
            kind='клиент', last_seen='2025-10-31',
            transcript=self.TALK + '\nя: зову на конференцию AGDays',
            today=self.TODAY)
        self.assertIn('тогда я тоже звал', text)

    def test_memory_wins_over_bridge(self):
        text, _ = softeners.build(
            'Зовём на конференцию AGDays.', name='Иван', address='вы',
            kind='клиент', last_seen='2025-10-31',
            transcript='я: зову на AGDays\nон: мне далеко ехать',
            today=self.TODAY)
        self.assertIn('далеко ехать', text)
        self.assertNotIn('Мы общались в октябре', text)


class TestInviteDetectionIsStrict(unittest.TestCase):
    """Приглашением считается приглашение, а не любое слово «мероприятие»."""

    BASE = 'Привет! Мы проводим AI Growth Day (ex AGDAYs) 28 августа.'

    def test_nominations_list_is_not_an_invite(self):
        # реальный случай: в сообщении был перечень номинаций премии
        talk = ('я: Сайты и приложения Партнерские программы Закрытие года '
                'Digital-офис Онлайн-мероприятие Мероприятие Позиционирование')
        self.assertFalse(openrouter.already_invited(self.BASE, talk))

    def test_named_event_is_an_invite(self):
        talk = 'я: в октябре у нас AGDays, приходи'
        self.assertTrue(openrouter.already_invited(self.BASE, talk))

    def test_event_word_with_invite_word(self):
        talk = 'я: зову на нашу конференцию в октябре'
        self.assertTrue(openrouter.already_invited(self.BASE, talk))

    def test_digital_alone_is_not_a_key(self):
        self.assertNotIn('digital', openrouter.invite_keys(
            'Конференция для digital-агентств'))
        self.assertNotIn('name', openrouter.invite_keys('Привет, {NAME}!'))

    def test_no_apology_when_there_was_no_invite(self):
        talk = ('я: вот все номинации в рамках рубрики Мероприятие\n'
                'он: ок')
        text, _note = softeners.build(self.BASE, name='Алексей', address='ты',
                                      kind='подрядчик', transcript=talk)
        self.assertNotIn('опять только с конфой', text)


class TestGender(unittest.TestCase):
    """Прошедшее время требует рода: «Ульяна, ты передавал» — ошибка."""

    def test_gender_by_name(self):
        self.assertEqual(names.gender('Ульяна'), 'ж')
        self.assertEqual(names.gender('Николай'), 'м')
        self.assertEqual(names.gender('Никита'), 'м')
        self.assertEqual(names.gender('Любовь'), 'ж')
        self.assertEqual(names.gender('Женя'), '')       # и так и так зовут
        self.assertEqual(names.gender('Кайл'), '')
        self.assertEqual(names.gender(''), '')

    def test_note_agrees_with_gender(self):
        self.assertIn('передавала', reactions.note('передаст', 'ты', 'ж'))
        self.assertIn('передавал ', reactions.note('передаст', 'ты', 'м'))
        # род неизвестен — оборот без прошедшего времени
        neutral = reactions.note('передаст', 'ты', '')
        self.assertNotIn('передавал', neutral)
        self.assertIn('коллегам', neutral)

    def test_you_form_needs_no_gender(self):
        self.assertEqual(reactions.note('передаст', 'вы', 'ж'),
                         reactions.note('передаст', 'вы', 'м'))

    def test_build_picks_gender_from_name(self):
        history = 'я: зову на AGDays\nон: передам коллегам'
        text, _ = softeners.build('Текст.', name='Ульяна', address='ты',
                                  kind='клиент', transcript=history)
        self.assertIn('передавала', text)
        self.assertNotIn('передавал ', text)


class TestSenderGender(unittest.TestCase):
    """Пишет не всегда мужчина: «пропустила», «звала», «буду рада»."""

    def test_swaps_only_words_about_self(self):
        self.assertEqual(softeners.to_sender('Пропустил, извини', 'ж'),
                         'Пропустила, извини')
        self.assertEqual(softeners.to_sender('буду рад видеть', 'ж'),
                         'буду рада видеть')
        self.assertEqual(softeners.to_sender('напишу им сам', 'ж'),
                         'напишу им сама')

    def test_male_is_left_alone(self):
        self.assertEqual(softeners.to_sender('Пропустил, извини', 'м'),
                         'Пропустил, извини')

    def test_similar_words_are_not_touched(self):
        # «ради», «самый», «звали» — не про род отправителя
        for text in ('ради интереса', 'самый первый', 'радость'):
            self.assertEqual(softeners.to_sender(text, 'ж'), text)

    def test_greeting_follows_sender(self):
        history = 'я: привет\nон: подскажи, а когда конференция?'
        text, _ = softeners.build('Текст письма.', name='Иван', address='ты',
                                  kind='коллеги', transcript=history,
                                  sender='ж')
        self.assertIn('пропустила', text.lower())

    def test_body_is_not_touched_by_sender_gender(self):
        body = 'Буду рад видеть на конференции.'
        text, _ = softeners.build(body, name='Иван', address='ты',
                                  kind='коллеги', sender='ж')
        self.assertIn('Буду рад видеть на конференции.', text)


class TestIntroInBridge(unittest.TestCase):
    """Малознакомому после года молчания важно, кто пишет."""

    TODAY = date(2026, 8, 14)
    TALK = ('я: добрый день, посмотрели смету\nон: да, вопросы есть\n'
            'я: давайте созвонимся\nон: удобно в четверг\n'
            'я: ок, поставил\nон: спасибо')

    def test_intro_goes_first(self):
        text, _ = softeners.build(
            'Зовём на конференцию.', name='Олег', address='вы', kind='клиент',
            last_seen='2025-10-31', recall='интеграцию с 1С',
            transcript=self.TALK, intro='Иван, CEO Alto', today=self.TODAY)
        self.assertIn('Напомню, я Иван, CEO Alto. Мы общались в октябре '
                      'прошлого года про интеграцию с 1С.', text)

    def test_without_intro_it_is_just_the_reminder(self):
        text, _ = softeners.build(
            'Зовём на конференцию.', name='Олег', address='вы', kind='клиент',
            last_seen='2025-10-31', recall='интеграцию с 1С',
            transcript=self.TALK, today=self.TODAY)
        self.assertIn('Мы общались в октябре прошлого года про интеграцию с 1С.',
                      text)
        self.assertNotIn('Напомню', text)

    def test_no_double_dot_after_intro(self):
        text, _ = softeners.build(
            'Текст.', name='Олег', address='вы', kind='клиент',
            last_seen='2025-10-31', intro='Иван, CEO Alto.', today=self.TODAY)
        self.assertNotIn('Alto..', text)


class TestWorksOnAFreshMachine(unittest.TestCase):
    """У коллеги на новом компьютере нет ни хранилища, ни наших скриптов."""

    def test_recall_topic_filters_junk(self):
        self.assertTrue(openrouter.empty_recall('обсуждение рабочих вопросов'))
        self.assertTrue(openrouter.empty_recall('поздравление с 23 февраля'))
        self.assertFalse(openrouter.empty_recall('сотрудничество по Битрикс24'))
        self.assertFalse(openrouter.empty_recall('интеграцию с 1С'))

    def test_event_words_come_from_the_invite_text(self):
        # мероприятие коллеги, про AGDays в коде ничего не знает
        base = 'Зовём на UralDigitalWeek 12 сентября, udw.ru'
        talk = ('я: приглашаю на UralDigitalWeek\n'
                'он: спасибо, но я не езжу по другим городам')
        keys = openrouter.invite_keys(base)
        self.assertEqual(reactions.from_transcript(talk, keys), 'далеко')
        # без примет из текста зашитый список это мероприятие не узнаёт
        self.assertEqual(reactions.from_transcript(talk), '')

    def test_key_helper_prefers_own_over_bundled(self):
        from app import config as config_module
        self.assertEqual(
            config_module.openrouter_key({'openrouter_key': ' sk-mine '}),
            'sk-mine')
        # без своего ключа берётся вшитый в сборку (в исходниках его нет)
        self.assertEqual(config_module.openrouter_key({'openrouter_key': ''}),
                         config_module.bundled_key())

    def test_model_helper_has_a_default(self):
        from app import config as config_module
        self.assertTrue(config_module.openrouter_model({}))
        self.assertEqual(config_module.openrouter_model({'openrouter_model': 'x'}),
                         'x')


class TestConnectionLoss(unittest.TestCase):
    """Прогон идёт часами: ноутбук успевает уснуть, wi-fi — смениться.

    Telethon после нескольких неудачных попыток бросает соединение
    насовсем, и дальше каждый запрос падает с «Cannot send requests while
    disconnected». Список от этого не кончается — программа успевает
    «обработать» весь остаток с ошибками. Так быть не должно.
    """

    class Contact(object):
        def __init__(self, name):
            self.name = name
            self.username = name
            self.label = '@' + name
            self.key = name
            self.source = ''
            self.message = ''
            self.peer = object()

    class Client(object):
        """Заглушка Telegram: связь можно рвать и чинить руками."""

        def __init__(self, connected=True, can_connect=True, fail_calls=0):
            self.connected = connected
            self.can_connect = can_connect
            self.fail_calls = fail_calls
            self.calls = 0
            self.connect_attempts = 0

        def is_connected(self):
            return self.connected

        async def connect(self):
            self.connect_attempts += 1
            if self.can_connect:
                self.connected = True

        async def __call__(self, request):
            self.calls += 1
            if self.calls <= self.fail_calls:
                self.connected = False
                raise ConnectionError('Cannot send requests while disconnected')
            return True

    def setUp(self):
        from app import drafts
        self.drafts = drafts
        self.pause = drafts.RECONNECT_PAUSE
        drafts.RECONNECT_PAUSE = 0        # чтобы тест не ждал по-настоящему
        self.folder = tempfile.TemporaryDirectory()
        self.original = progress.STATE_DIR
        progress.STATE_DIR = Path(self.folder.name)

    def tearDown(self):
        self.drafts.RECONNECT_PAUSE = self.pause
        progress.STATE_DIR = self.original
        self.folder.cleanup()

    def run_drafts(self, client, count=5, **kwargs):
        import asyncio

        events = []
        contacts = [self.Contact('c{}'.format(n)) for n in range(count)]
        summary = asyncio.run(self.drafts.create_drafts(
            client=client, contacts=contacts, variants=['Привет!'],
            contacts_file='', delay_range=(0, 0),
            on_event=lambda kind, **data: events.append(kind),
            **kwargs))
        return summary, events

    def test_dead_connection_stops_the_run(self):
        client = self.Client(connected=False, can_connect=False)
        with self.assertRaises(self.drafts.RunAborted) as caught:
            self.run_drafts(client)
        self.assertIn('Связь с Telegram', str(caught.exception))
        # ни одного черновика и ни одной попытки записи
        self.assertEqual(client.calls, 0)
        # попытались починить, а не сдались молча
        self.assertEqual(client.connect_attempts, self.drafts.RECONNECT_ATTEMPTS)

    def test_connection_is_restored_and_run_continues(self):
        client = self.Client(connected=False, can_connect=True)
        summary, events = self.run_drafts(client, count=3)
        self.assertEqual(summary.created, 3)
        self.assertIn('reconnected', events)

    def test_break_on_a_request_costs_one_contact(self):
        # связь рвётся на первом же черновике и тут же чинится
        client = self.Client(fail_calls=1)
        summary, _events = self.run_drafts(client, count=3)
        self.assertEqual(summary.failed, 1)
        self.assertEqual(summary.created, 2)

    def test_unreadable_history_is_not_an_empty_chat(self):
        # None от tgcontacts.history раньше падал в as_transcript
        # («'NoneType' object is not iterable») на каждом контакте
        with self.assertRaises(TypeError):
            tgcontacts.as_transcript(None)
        self.assertEqual(tgcontacts.as_transcript([]), '')


class TestKeyIsNotLostOnSave(unittest.TestCase):
    """Ключ, совпавший с вшитым в сборку, стирать нельзя.

    У того, кто собирает программу, свой ключ и вшитый — один и тот же.
    Сохранение настроек стирало его из credentials.json, а следующая сборка
    брала ключ оттуда же: получалась сборка без ключа и настройки без ключа.
    """

    def setUp(self):
        from app import config as config_module
        from app import paths
        self.config = config_module
        self.paths = paths
        self.folder = tempfile.TemporaryDirectory()
        self.saved = (config_module.CREDENTIALS_PATH, paths.DATA_DIR,
                      paths.CONTACTS_DIR, paths.STATE_DIR, paths.TEMPLATES_DIR)
        root = Path(self.folder.name)
        for name in ('DATA_DIR', 'CONTACTS_DIR', 'STATE_DIR', 'TEMPLATES_DIR'):
            setattr(paths, name, root / name.lower())
        config_module.CREDENTIALS_PATH = root / 'credentials.json'

    def tearDown(self):
        (self.config.CREDENTIALS_PATH, self.paths.DATA_DIR,
         self.paths.CONTACTS_DIR, self.paths.STATE_DIR,
         self.paths.TEMPLATES_DIR) = self.saved
        self.folder.cleanup()

    def test_own_key_survives_even_if_it_equals_the_bundled_one(self):
        own = self.config.bundled_key() or 'sk-or-v1-' + 'a' * 64
        self.config.save({'api_id': 1, 'openrouter_key': own})
        stored = json.loads(self.config.CREDENTIALS_PATH.read_text(encoding='utf-8'))
        self.assertEqual(stored['openrouter_key'], own)


class TestUsefulOpening(unittest.TestCase):
    """Рабочий заход владельца — короткое обещание пользы.

    Разбор 140 его приглашений: компанию собеседника он не назвал ни разу,
    должность — трижды. Зато «может тебе будет полезно» пишет постоянно.
    Прежняя версия ставила «Ты же CEO в X — там как раз секция…»; эти
    тесты стерегут, чтобы такое не вернулось.
    """

    BASE = 'Проводим AI Growth Day. Программа — agday.ru'

    def test_informal_gets_a_short_promise(self):
        text, note = softeners.build(self.BASE, name='Андрей', address='ты',
                                     kind='ит и digital')
        opening = text.split('\n\n')[0]
        self.assertIn('Андрей, привет!', opening)
        self.assertTrue(any(mark in opening for mark in
                            ('полезно', 'интересно', 'актуально')), opening)
        self.assertIn('полезно', note.replace('интересно', 'полезно'))

    def test_formal_gets_no_smiley(self):
        text, _note = softeners.build(self.BASE, name='Роман', address='вы',
                                      kind='клиент')
        opening = text.split('\n\n')[0]
        self.assertNotIn(')', opening.replace('Роман, добрый день!', ''))

    def test_company_and_role_never_appear(self):
        text, _note = softeners.build(self.BASE, name='Андрей', address='ты',
                                      kind='ит и digital')
        self.assertNotIn('CEO', text)
        self.assertNotIn('секция', text)

    def test_other_segments_are_left_alone(self):
        for kind in ('подрядчик', 'соискатель', 'другое', ''):
            text, _note = softeners.build(self.BASE, name='Пётр', address='ты',
                                          kind=kind)
            self.assertEqual(text.split('\n\n')[0], 'Пётр, привет!')

    def test_variant_is_stable_for_the_same_person(self):
        first = softeners.useful('рабочий', seed='Андрей')
        self.assertEqual(first, softeners.useful('рабочий', seed='Андрей'))

    def test_promise_yields_to_anything_concrete(self):
        # помним прошлый ответ — дежурная фраза сверху не нужна
        text, _note = softeners.build(
            self.BASE, name='Максим', address='ты', kind='ит и digital',
            transcript='я: зову на AI Growth Day agday.ru\nон: далеко ехать')
        opening = text.split('\n\n')[0]
        self.assertIn('далеко ехать', opening)
        self.assertNotIn('полезно', opening)


class TestSpeakerAgain(unittest.TestCase):
    """Звали выступить — зовём снова, но с условием."""

    BASE = 'Проводим AI Growth Day. Программа — agday.ru'
    TALK = ('я: мы 28 августа проводим конференцию, может интересно будет '
            'выступить\nон: спасибо, подумаю')

    def test_past_speaker_invite_is_found(self):
        self.assertTrue(softeners.invited_as_speaker(self.TALK))
        self.assertTrue(softeners.invited_as_speaker(
            'я: если есть кейсы по ИИ — подавай доклад'))
        self.assertFalse(softeners.invited_as_speaker(
            'он: я бы хотел выступить у вас'))
        self.assertFalse(softeners.invited_as_speaker('я: приходи, будем рады'))

    def test_speaker_hook_has_a_condition(self):
        text, note = softeners.build(self.BASE, name='Олег', address='ты',
                                     kind='ит и digital', transcript=self.TALK)
        self.assertIn('звал тебя спикером', text)
        self.assertIn('если с ИИ у вас что-то внедрили', text)
        self.assertIn('спикером', note)

    def test_only_for_the_two_segments(self):
        # спикеров ищут среди клиентов и заметных ит-компаний
        text, _note = softeners.build(self.BASE, name='Олег', address='ты',
                                      kind='подрядчик', transcript=self.TALK)
        self.assertNotIn('спикером', text)

    def test_speaker_replaces_the_repeat_apology(self):
        text, _note = softeners.build(
            self.BASE, name='Олег', address='ты', kind='ит и digital',
            transcript='я: зову на AI Growth Day agday.ru, может выступишь?')
        self.assertIn('звал тебя спикером', text)
        self.assertNotIn('опять только с конфой', text)


class TestQuestionAtTheEnd(unittest.TestCase):
    """Вопрос собеседнику — в 15 письмах из 140, и он работает на ответ."""

    BASE = 'Проводим AI Growth Day. Программа — agday.ru'
    REAL = ('я: привет, как проект\nон: запускаемся\n'
            'я: а сроки\nон: неделю подвинули\n'
            'я: понял\nон: ага')
    SAM = REAL + '\nя: а ты как сам?\nон: норм'
    BACK = REAL + '\nон: а у тебя как дела?\nя: норм'

    def test_asked_only_of_people_we_actually_talked_to(self):
        text, _note = softeners.build(self.BASE, name='Денис', address='ты',
                                      kind='ит и digital', transcript=self.REAL)
        self.assertIn('Как у тебя дела?', text)

    def test_no_a_without_a_question_about_us(self):
        # «А у тебя как дела?» — это ответ; без вопроса про нас «а» повисает
        plain, _ = softeners.build(self.BASE, name='Денис', address='ты',
                                   kind='ит и digital', transcript=self.REAL)
        self.assertIn('Как у тебя дела?', plain)
        self.assertNotIn('А у тебя', plain)
        back, _ = softeners.build(self.BASE, name='Денис', address='ты',
                                  kind='ит и digital', transcript=self.BACK)
        self.assertIn('А у тебя как дела?', back)

    def test_sam_only_if_it_was_already_said(self):
        # «как сам» — оборот не для всех: только если так уже говорили
        plain, _ = softeners.build(self.BASE, name='Денис', address='ты',
                                   kind='ит и digital', transcript=self.REAL)
        self.assertNotIn('как сам', plain)
        known, _ = softeners.build(self.BASE, name='Денис', address='ты',
                                   kind='ит и digital', transcript=self.SAM)
        self.assertIn('Ты как сам?', known)

    def test_not_asked_of_strangers(self):
        text, _note = softeners.build(self.BASE, name='Денис', address='ты',
                                      kind='ит и digital', transcript='')
        self.assertNotIn('как сам', text)

    def test_gender_belongs_to_the_recipient(self):
        # «сам» — про собеседника: Елене нельзя писать «И ты как сам?»
        for name, expected in (('Елена', 'И ты как сама?'),
                               ('Лена', 'И ты как сама?'),
                               ('Иван', 'И ты как сам?')):
            text, _note = softeners.build(
                self.BASE, name=name, address='ты', kind='ит и digital',
                transcript=self.BACK + '\nя: а ты как сам?')
            self.assertIn(expected, text, name)

    def test_unknown_gender_avoids_the_word(self):
        text, _note = softeners.build(self.BASE, name='Саша', address='ты',
                                      kind='ит и digital', transcript=self.SAM)
        self.assertIn('Как у тебя дела?', text)

    def test_sender_gender_does_not_touch_it(self):
        # род отправителя меняет «звал/звала», но не «сам» про собеседника
        text, _note = softeners.build(
            self.BASE, name='Иван', address='ты', kind='ит и digital',
            transcript=self.BACK + '\nя: а ты как сам?', sender='ж')
        self.assertIn('И ты как сам?', text)

    def test_never_in_the_formal_register(self):
        text, _note = softeners.build(self.BASE, name='Денис', address='вы',
                                      kind='клиент', transcript=self.SAM)
        self.assertNotIn('как сам', text)
        self.assertNotIn('как дела', text)


class TestIntroduction(unittest.TestCase):
    """«Меня Иван зовут, я организатор AI Growth Day и гендиректор Alto»."""

    def test_full_formula(self):
        self.assertEqual(
            softeners.introduce('организатор AI Growth Day и гендиректор Alto',
                                'Иван'),
            'Меня Иван зовут, я организатор AI Growth Day и гендиректор Alto.')

    def test_without_a_name_it_falls_back(self):
        self.assertEqual(softeners.introduce('CEO Alto'), 'Напомню, я CEO Alto.')

    def test_nothing_to_say(self):
        self.assertEqual(softeners.introduce('', 'Иван'), '')


class TestLongSilenceNeedsRealTalk(unittest.TestCase):
    """«Давно не общались» — только если было что прерывать."""

    BASE = 'Проводим AI Growth Day. Программа — agday.ru'
    TODAY = date(2026, 8, 14)
    REAL = ('я: привет, как дела с проектом\nон: нормально, запускаемся\n'
            'я: круто, а сроки не поехали\nон: неделю подвинули\n'
            'я: понял, тогда до связи\nон: ага, спасибо')
    THIN = 'я: привет, есть вопрос по вёрстке\nон: ок, напишу завтра'

    def test_real_talk_is_recognised(self):
        self.assertTrue(softeners.had_real_talk(self.REAL))
        self.assertFalse(softeners.had_real_talk(self.THIN))
        self.assertFalse(softeners.had_real_talk(''))

    def test_year_of_silence_after_a_real_talk(self):
        text, note = softeners.build(
            self.BASE, name='Коля', address='ты', kind='ит и digital',
            transcript=self.REAL, last_seen='2024-09-10', today=self.TODAY)
        self.assertIn('Давно не общались', text)
        self.assertIn('молчания', note)

    def test_a_couple_of_messages_get_no_such_claim(self):
        text, _note = softeners.build(
            self.BASE, name='Коля', address='ты', kind='ит и digital',
            transcript=self.THIN, last_seen='2024-09-10', today=self.TODAY)
        self.assertNotIn('Давно не общались', text)
        self.assertNotIn('Мы общались', text)

    def test_stranger_still_gets_introduced(self):
        # переписки не было, но после года молчания надо назваться
        text, _note = softeners.build(
            self.BASE, name='Дарья', address='вы', kind='ит и digital',
            transcript=self.THIN, last_seen='2024-09-10', today=self.TODAY,
            intro='Иван из Alto')
        self.assertIn('Напомню, я Иван из Alto.', text)

    def test_topic_of_the_last_talk_is_used(self):
        text, _note = softeners.build(
            self.BASE, name='Евгения', address='вы', kind='клиент',
            transcript=self.REAL, last_seen='2025-02-10', today=self.TODAY,
            recall='подбор подрядчика на сайт')
        self.assertIn('в прошлый раз обсуждали подбор подрядчика на сайт', text)

    def test_half_a_year_keeps_the_dated_bridge(self):
        text, _note = softeners.build(
            self.BASE, name='Сергей', address='вы', kind='ит и digital',
            transcript=self.REAL, last_seen='2025-11-10', today=self.TODAY,
            recall='доклад на конференции')
        self.assertIn('Мы общались в ноябре прошлого года', text)


class TestNameFromTheDialogCanBeWrong(unittest.TestCase):
    """Разбор иногда берёт имя того, о ком речь, а не собеседника."""

    def test_gender_clash_means_a_mix_up(self):
        # чат «Anna», а модель вычитала в переписке «Дмитрий Южанин»
        self.assertEqual(names.trusted('Дмитрий', 'Anna'), 'Анна')
        self.assertEqual(names.trusted('Иван', 'Olga alpinabook.ru'), 'Ольга')

    def test_short_forms_are_left_alone(self):
        self.assertEqual(names.trusted('Максим', 'Макс Десятых'), 'Максим')
        self.assertEqual(names.trusted('Катя', 'Ekaterina Кнопка'), 'Катя')
        self.assertEqual(names.trusted('Женя', 'Evgeniya Kazakova[RetailTech]'),
                         'Женя')

    def test_unknown_names_are_not_touched(self):
        self.assertEqual(names.trusted('Гуррагча', 'Zhugderdemidiin'),
                         'Гуррагча')
        self.assertEqual(names.trusted('', 'Anna'), '')


class TestWhoGoesFirst(unittest.TestCase):
    """Порядок рассылки: свежесть, плотность, частота, неформальность."""

    TODAY = date(2026, 8, 18)

    def rows(self, mine, theirs, days, text='ок'):
        """Переписка: столько-то сообщений, размазанных на столько-то дней."""
        from datetime import timedelta
        start = self.TODAY - timedelta(days=days)
        total = max(1, mine + theirs - 1)
        out = []
        for n in range(mine + theirs):
            when = start + timedelta(days=int(days * n / total))
            out.append((n < mine, when, text))
        return out

    def test_stats_counts_both_sides(self):
        warm = priority.stats(self.rows(3, 4, 30))
        self.assertEqual(warm['msgs'], 7)
        self.assertEqual(warm['mine'], 3)
        self.assertEqual(warm['theirs'], 4)
        self.assertGreater(warm['span'], 0)

    def test_one_sided_blast_is_not_frequency(self):
        # двадцать наших сообщений в пустоту — это не общение
        self.assertEqual(priority.frequency(priority.stats(self.rows(20, 0, 60))),
                         0.0)
        self.assertGreater(priority.frequency(priority.stats(self.rows(10, 10, 60))),
                           0.5)

    def test_density_is_messages_per_month(self):
        # шесть сообщений, размазанных на год, — это не плотное общение
        thin = priority.density(priority.stats(self.rows(3, 3, 330)))
        dense = priority.density(priority.stats(self.rows(15, 15, 60)))
        self.assertLess(thin, dense)
        self.assertLess(thin, 0.3)

    def test_informality_leans_on_ty(self):
        warm = priority.stats(self.rows(3, 3, 30, 'ок, давай'))
        self.assertGreater(priority.informality(warm, 'ты'),
                           priority.informality(warm, 'вы'))

    def test_formal_greetings_pull_it_down(self):
        loose = priority.stats(self.rows(3, 3, 30, 'ага, давай'))
        stiff = priority.stats(self.rows(3, 3, 30,
                                         'Добрый день! Направляю с уважением.'))
        self.assertGreater(priority.informality(loose, 'ты'),
                           priority.informality(stiff, 'ты'))

    def test_fresh_and_warm_outranks_old_and_cold(self):
        warm_person = {'date': '2026-07-01', 'address': 'ты',
                       'warm': priority.stats(self.rows(12, 12, 90, 'ага)'))}
        cold_person = {'date': '2022-01-01', 'address': 'вы',
                       'warm': priority.stats(self.rows(1, 1, 1,
                                                        'Добрый день'))}
        self.assertGreater(priority.score(warm_person, self.TODAY),
                           priority.score(cold_person, self.TODAY))

    def test_score_survives_an_empty_record(self):
        self.assertEqual(priority.score({}, self.TODAY), 0)
        self.assertEqual(priority.label(0), '○○○')

    def test_date_alone_still_ranks(self):
        # теплоту ещё не измеряли — сортируем хотя бы по свежести
        fresh = priority.score({'date': '2026-08-01'}, self.TODAY)
        stale = priority.score({'date': '2021-08-01'}, self.TODAY)
        self.assertGreater(fresh, stale)


class TestRecallIsAboutTheSubject(unittest.TestCase):
    """«Выбор подрядчика» — это стадия переговоров, а не предмет."""

    def test_process_without_a_subject_is_dropped(self):
        for text in ('выбор подрядчика', 'подбор разработчиков',
                     'обсуждение сметы', 'стоимость часа',
                     'возможное сотрудничество'):
            self.assertTrue(openrouter.empty_recall(text), text)

    def test_process_with_a_subject_survives(self):
        for text in ('подбор разработчиков Laravel для маркетплейса',
                     'смету на редизайн сайта',
                     'стоимость часа команды на портал'):
            self.assertFalse(openrouter.empty_recall(text), text)

    def test_real_subjects_are_kept(self):
        for text in ('интеграцию с 1С', 'ваш доклад на РИФе',
                     'переезд сайта на Битрикс'):
            self.assertFalse(openrouter.empty_recall(text), text)


class TestUpdates(unittest.TestCase):
    """Обновление в один клик: сравнение версий и безопасная подмена."""

    def test_version_compare(self):
        from app import updates
        self.assertTrue(updates.is_newer('2.5.0', '2.4.9'))
        self.assertTrue(updates.is_newer('2.10.0', '2.9.9'))
        self.assertFalse(updates.is_newer('2.4.1', '2.4.1'))
        self.assertFalse(updates.is_newer('2.3.0', '2.4.1'))

    def test_garbage_version_does_not_crash(self):
        from app import updates
        self.assertEqual(updates.as_numbers('v3.0-beta'), (3, 0, 0))
        self.assertEqual(updates.as_numbers(''), (0, 0, 0))
        self.assertEqual(updates.as_numbers(None), (0, 0, 0))

    def test_login_covers_the_whole_site(self):
        # архив лежит не по адресу манифеста: логин, привязанный к
        # latest.json, на скачивание бы не распространился
        from app import updates
        self.assertTrue(updates.FEED_URL.startswith(updates.SITE_URL))

    def test_swap_builds_the_copy_before_touching_the_old_one(self):
        # порядок «сначала отодвинуть, потом копировать» при сбое оставлял
        # человека вообще без программы
        from app import updates
        script = updates.SWAP
        self.assertLess(script.index('ditto "{source}" "{target}.new"'),
                        script.index('mv "{target}" "{target}.old"'))
        self.assertIn('mv "{target}.old" "{target}"', script)

    def test_swap_refuses_a_missing_source(self):
        from app import updates
        self.assertIn('[ -d "{source}" ] || exit 1', updates.SWAP)


class TestMacVersionShim(unittest.TestCase):
    """Telethon падал при импорте, если macOS назвала версию одним числом.

    Реальный случай: на одном Mac platform.mac_ver() вернул «26» вместо
    «26.5.2», и `ver, major, *_ = release.split('.')` внутри telethon дал
    ValueError. Ловится он там только как OSError, поэтому валился весь
    импорт — программа закрывалась до появления окна.
    """

    def setUp(self):
        import platform
        self.original = platform.mac_ver

    def tearDown(self):
        import platform
        platform.mac_ver = self.original

    def patched(self, release):
        import importlib
        import platform
        platform.mac_ver = self.original
        platform.mac_ver = lambda: (release, ('', '', ''), 'arm64')
        import app
        app._fix_mac_version()
        return platform.mac_ver()[0]

    def test_short_version_is_padded(self):
        self.assertEqual(self.patched('26'), '26.0.0')
        self.assertEqual(self.patched('11'), '11.0.0')

    def test_full_version_is_left_alone(self):
        self.assertEqual(self.patched('26.5.2'), '26.5.2')

    def test_unknown_version_reads_as_modern(self):
        # версию не узнали — берём путь для современных macOS, он безопаснее
        self.assertEqual(self.patched(''), '99.0.0')
        self.assertEqual(self.patched('unknown'), '99.0.0')

    def test_telethon_imports_on_a_short_version(self):
        import subprocess
        import sys
        code = ('import platform\n'
                'platform.mac_ver = lambda: ("26", ("", "", ""), "arm64")\n'
                'import app\n'
                'from app import session\n'
                'print("ok")\n')
        out = subprocess.run([sys.executable, '-c', code], capture_output=True,
                             text=True,
                             cwd=str(Path(__file__).resolve().parent.parent))
        self.assertEqual(out.stdout.strip(), 'ok', out.stderr[-400:])
