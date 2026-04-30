import pytest
from bstrong.utils import fix_phone_number


class TestFixPhoneNumber:
    def test_us_10_digit_no_country_code(self):
        result = fix_phone_number('5085551234')
        assert result == {'valid': True, 'number': '+15085551234'}

    def test_us_11_digit_with_country_code(self):
        result = fix_phone_number('15085551234')
        assert result == {'valid': True, 'number': '+15085551234'}

    def test_already_e164(self):
        result = fix_phone_number('+15085551234')
        assert result == {'valid': True, 'number': '+15085551234'}

    def test_none_returns_invalid(self):
        result = fix_phone_number(None)
        assert result == {'valid': False, 'number': None}

    def test_empty_string_returns_invalid(self):
        result = fix_phone_number('')
        assert result == {'valid': False, 'number': None}

    def test_letters_returns_invalid(self):
        result = fix_phone_number('notaphone')
        assert result['valid'] is False

    def test_too_short_returns_invalid(self):
        result = fix_phone_number('12345')
        assert result['valid'] is False

    def test_international_uk_number(self):
        result = fix_phone_number('+447911123456')
        assert result['valid'] is True
        assert result['number'] == '+447911123456'

    def test_number_with_whitespace_stripped(self):
        result = fix_phone_number('  5085551234  ')
        assert result == {'valid': True, 'number': '+15085551234'}

    def test_developer_phone_number_valid(self):
        result = fix_phone_number('7745218808')
        assert result == {'valid': True, 'number': '+17745218808'}
