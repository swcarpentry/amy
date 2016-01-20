# coding: utf-8
import cgi
import datetime
from io import StringIO

from django.contrib.auth.models import Group
from django.contrib.sessions.serializers import JSONSerializer
from django.test import TestCase, RequestFactory
from django.core.urlresolvers import reverse

from ..models import Host, Event, Role, Person, Task, Badge, Award
from ..util import (
    upload_person_task_csv,
    verify_upload_person_task,
    generate_url_to_event_index,
    find_tags_on_event_index,
    find_tags_on_event_website,
    parse_tags_from_event_website,
    validate_tags_from_event_website,
    get_members,
    default_membership_cutoff,
    assignment_selection,
)

from .base import TestBase


class UploadPersonTaskCSVTestCase(TestCase):

    def compute_from_string(self, csv_str):
        ''' wrap up buffering the raw string & parsing '''
        csv_buf = StringIO(csv_str)
        # compute and return
        return upload_person_task_csv(csv_buf)

    def test_basic_parsing(self):
        ''' See Person.PERSON_UPLOAD_FIELDS for field ordering '''
        csv = """personal,family,email
john,doe,johndoe@email.com
jane,doe,janedoe@email.com"""
        person_tasks, _ = self.compute_from_string(csv)

        # assert
        self.assertEqual(len(person_tasks), 2)

        person = person_tasks[0]
        self.assertTrue(set(person.keys()).issuperset(set(Person.PERSON_UPLOAD_FIELDS)))

    def test_csv_without_required_field(self):
        ''' All fields in Person.PERSON_UPLOAD_FIELDS must be in csv '''
        bad_csv = """personal,family
john,doe"""
        person_tasks, empty_fields = self.compute_from_string(bad_csv)
        self.assertTrue('email' in empty_fields)

    def test_csv_with_mislabeled_field(self):
        ''' It pays to be strict '''
        bad_csv = """personal,family,emailaddress
john,doe,john@doe.com"""
        person_tasks, empty_fields = self.compute_from_string(bad_csv)
        self.assertTrue('email' in empty_fields)

    def test_csv_with_empty_lines(self):
        csv = """personal,family,emailaddress
john,doe,john@doe.com
,,"""
        person_tasks, empty_fields = self.compute_from_string(csv)
        self.assertEqual(len(person_tasks), 1)
        person = person_tasks[0]
        self.assertEqual(person['personal'], 'john')

    def test_empty_field(self):
        ''' Ensure we don't mis-order fields given blank data '''
        csv = """personal,family,email
john,,johndoe@email.com"""
        person_tasks, _ = self.compute_from_string(csv)
        person = person_tasks[0]
        self.assertEqual(person['family'], '')

    def test_serializability_of_parsed(self):
        csv = """personal,family,email
john,doe,johndoe@email.com
jane,doe,janedoe@email.com"""
        person_tasks, _ = self.compute_from_string(csv)

        try:
            serializer = JSONSerializer()
            serializer.dumps(person_tasks)
        except TypeError:
            self.fail('Dumping person_tasks to JSON unexpectedly failed!')

    def test_malformed_CSV_with_proper_header_row(self):
        csv = """personal,family,email
This is a malformed CSV
        """
        person_tasks, empty_fields = self.compute_from_string(csv)
        self.assertEqual(person_tasks[0]["personal"],
                         "This is a malformed CSV")
        self.assertEqual(set(empty_fields),
                         set(["family", "email"]))


class CSVBulkUploadTestBase(TestBase):
    """
    Simply provide necessary setUp and make_data functions that are used in two
    different TestCases
    """
    def setUp(self):
        super(CSVBulkUploadTestBase, self).setUp()
        test_host = Host.objects.create(domain='example.com',
                                        fullname='Test Host')

        Role.objects.create(name='Instructor')
        Role.objects.create(name='learner')
        Event.objects.create(start=datetime.date.today(),
                             host=test_host,
                             slug='foobar',
                             admin_fee=100)

        self._setUpUsersAndLogin()

    def make_csv_data(self):
        """
        Sample CSV data
        """
        return """personal,family,email,event,role
John,Doe,notin@db.com,foobar,Instructor
"""

    def make_data(self):
        csv_str = self.make_csv_data()
        # upload_person_task_csv gets thoroughly tested in
        # UploadPersonTaskCSVTestCase
        data, _ = upload_person_task_csv(StringIO(csv_str))
        return data


class VerifyUploadPersonTask(CSVBulkUploadTestBase):

    ''' Scenarios to test:
        - Everything is good
        - no 'person' key
        - event DNE
        - role DNE
        - email already exists
    '''

    def test_verify_with_good_data(self):
        good_data = self.make_data()
        has_errors = verify_upload_person_task(good_data)
        self.assertFalse(has_errors)
        # make sure 'errors' wasn't set
        self.assertIsNone(good_data[0]['errors'])

    def test_verify_event_doesnt_exist(self):
        bad_data = self.make_data()
        bad_data[0]['event'] = 'no-such-event'
        has_errors = verify_upload_person_task(bad_data)
        self.assertTrue(has_errors)

        errors = bad_data[0]['errors']
        self.assertTrue(len(errors) == 1)
        self.assertTrue('Event with slug' in errors[0])

    def test_verify_role_doesnt_exist(self):
        bad_data = self.make_data()
        bad_data[0]['role'] = 'foobar'

        has_errors = verify_upload_person_task(bad_data)
        self.assertTrue(has_errors)

        errors = bad_data[0]['errors']
        self.assertTrue(len(errors) == 1)
        self.assertTrue('Role with name' in errors[0])

    def test_verify_email_caseinsensitive_matches(self):
        bad_data = self.make_data()
        # test both matching and case-insensitive matching
        for email in ('harry@hogwarts.edu', 'HARRY@hogwarts.edu'):
            bad_data[0]['email'] = email
            bad_data[0]['personal'] = 'Harry'
            bad_data[0]['family'] = 'Potter'

            has_errors = verify_upload_person_task(bad_data)
            self.assertFalse(has_errors)

    def test_verify_name_matching_existing_user(self):
        bad_data = self.make_data()
        bad_data[0]['email'] = 'harry@hogwarts.edu'
        has_errors = verify_upload_person_task(bad_data)
        self.assertTrue(has_errors)
        errors = bad_data[0]['errors']
        self.assertEqual(len(errors), 2)
        self.assertTrue('personal' in errors[0])
        self.assertTrue('family' in errors[1])

    def test_verify_existing_user_has_workshop_role_provided(self):
        bad_data = [
            {
                'email': 'harry@hogwarts.edu',
                'personal': 'Harry',
                'family': 'Potter',
                'event': '',
                'role': '',
            }
        ]
        has_errors = verify_upload_person_task(bad_data)
        self.assertTrue(has_errors)
        errors = bad_data[0]['errors']
        self.assertEqual(len(errors), 2)
        self.assertIn('Must have a role', errors[0])
        self.assertIn('Must have an event', errors[1])

    def test_username_from_existing_person(self):
        """Make sure the username is being changed for correct one."""
        data = [
            {
                'personal': 'Harry',
                'family': 'Potter',
                'username': 'wrong_username',
                'email': 'harry@hogwarts.edu',
                'event': '',
                'role': '',
            }
        ]
        verify_upload_person_task(data)
        self.assertEqual('potter.h', data[0]['username'])

    def test_username_from_nonexisting_person(self):
        """Make sure the username is not being changed."""
        data = [
            {
                'personal': 'Harry',
                'family': 'Frotter',
                'username': 'supplied_username',
                'email': 'h.frotter@hogwarts.edu',
                'event': '',
                'role': '',
            }
        ]
        verify_upload_person_task(data)
        self.assertEqual('supplied_username', data[0]['username'])


class BulkUploadUsersViewTestCase(CSVBulkUploadTestBase):

    def setUp(self):
        super().setUp()
        Role.objects.create(name='Helper')

    def test_event_name_dropped(self):
        """
        Test for regression:
        test whether event name is really getting empty when user changes it
        from "foobar" to empty.
        """
        data = self.make_data()

        # self.client is authenticated user so we have access to the session
        store = self.client.session
        store['bulk-add-people'] = data
        store.save()

        # send exactly what's in 'data', except for the 'event' field: leave
        # this one empty
        payload = {
            "personal": data[0]['personal'],
            "family": data[0]['family'],
            "username": data[0]['username'],
            "email": data[0]['email'],
            "event": "",
            "role": "",
            "verify": "Verify",
        }
        rv = self.client.post(reverse('person_bulk_add_confirmation'), payload)
        self.assertEqual(rv.status_code, 200)
        _, params = cgi.parse_header(rv['content-type'])
        charset = params['charset']
        content = rv.content.decode(charset)
        self.assertNotIn('foobar', content)

    def test_upload_existing_user(self):
        """
        Check if uploading existing users ends up with them having new role
        assigned.

        This is a special case of upload feature: if user uploads a person that
        already exists we should only assign new role and event to that person.
        """
        csv = """personal,family,email,event,role
Harry,Potter,harry@hogwarts.edu,foobar,Helper
"""
        data, _ = upload_person_task_csv(StringIO(csv))

        # self.client is authenticated user so we have access to the session
        store = self.client.session
        store['bulk-add-people'] = data
        store.save()

        # send exactly what's in 'data'
        payload = {
            "personal": data[0]['personal'],
            "family": data[0]['family'],
            "email": data[0]['email'],
            "event": data[0]['event'],
            "role": data[0]['role'],
            "confirm": "Confirm",
        }

        people_pre = set(Person.objects.all())
        tasks_pre = set(Task.objects.filter(person=self.harry,
                                            event__slug="foobar"))

        rv = self.client.post(reverse('person_bulk_add_confirmation'), payload,
                              follow=True)
        self.assertEqual(rv.status_code, 200)

        people_post = set(Person.objects.all())
        tasks_post = set(Task.objects.filter(person=self.harry,
                                             event__slug="foobar"))

        # make sure no-one new was added
        self.assertSetEqual(people_pre, people_post)

        # make sure that Harry was assigned a new role
        self.assertNotEqual(tasks_pre, tasks_post)

    def test_upload_existing_user_existing_task(self):
        """
        Check if uploading existing user and assigning existing task to that
        user is silent (ie. no Task nor Person is being created).
        """
        foobar = Event.objects.get(slug="foobar")
        instructor = Role.objects.get(name="Instructor")
        Task.objects.create(person=self.harry, event=foobar, role=instructor)

        csv = """personal,family,email,event,role
Harry,Potter,harry@hogwarts.edu,foobar,Instructor
"""
        data, _ = upload_person_task_csv(StringIO(csv))

        # self.client is authenticated user so we have access to the session
        store = self.client.session
        store['bulk-add-people'] = data
        store.save()

        # send exactly what's in 'data'
        payload = {
            "personal": data[0]['personal'],
            "family": data[0]['family'],
            "email": data[0]['email'],
            "event": data[0]['event'],
            "role": data[0]['role'],
            "confirm": "Confirm",
        }

        tasks_pre = set(Task.objects.filter(person=self.harry,
                                            event__slug="foobar"))
        users_pre = set(Person.objects.all())

        rv = self.client.post(reverse('person_bulk_add_confirmation'), payload,
                              follow=True)

        tasks_post = set(Task.objects.filter(person=self.harry,
                                             event__slug="foobar"))
        users_post = set(Person.objects.all())
        self.assertEqual(tasks_pre, tasks_post)
        self.assertEqual(users_pre, users_post)
        self.assertEqual(rv.status_code, 200)

    def test_attendance_increases(self):
        """
        Check if uploading tasks with role "learner" increase event's
        attendance.
        """
        foobar = Event.objects.get(slug="foobar")
        assert foobar.attendance is None
        foobar.save()

        csv = """personal,family,email,event,role
Harry,Potter,harry@hogwarts.edu,foobar,learner
"""
        data, _ = upload_person_task_csv(StringIO(csv))

        # self.client is authenticated user so we have access to the session
        store = self.client.session
        store['bulk-add-people'] = data
        store.save()

        # send exactly what's in 'data'
        payload = {
            "personal": data[0]['personal'],
            "family": data[0]['family'],
            "email": data[0]['email'],
            "event": data[0]['event'],
            "role": data[0]['role'],
            "confirm": "Confirm",
        }

        self.client.post(reverse('person_bulk_add_confirmation'), payload,
                         follow=True)

        foobar.refresh_from_db()
        self.assertEqual(1, foobar.attendance)


class TestHandlingEventTags(TestCase):
    maxDiff = None

    def test_generating_url_to_index(self):
        tests = [
            'http://swcarpentry.github.io/workshop-template',
            'https://swcarpentry.github.com/workshop-template',
            'http://swcarpentry.github.com/workshop-template/',
        ]
        expected_url = ('https://raw.githubusercontent.com/swcarpentry/'
                        'workshop-template/gh-pages/index.html')
        expected_repo = 'workshop-template'
        for url in tests:
            with self.subTest(url=url):
                url, repo = generate_url_to_event_index(url)
                self.assertEqual(expected_url, url)
                self.assertEqual(expected_repo, repo)

    def test_finding_tags_on_index(self):
        content = """---
layout: workshop
root: .
venue: Euphoric State University
address: Highway to Heaven 42, Academipolis
country: us
language: us
latlng: 36.998977, -109.045173
humandate: Jul 13-14, 2015
humantime: 9:00 - 17:00
startdate: 2015-07-13
enddate: "2015-07-14"
instructor: ["Hermione Granger", "Ron Weasley",]
helper: ["Peter Parker", "Tony Stark", "Natasha Romanova",]
contact: hermione@granger.co.uk, rweasley@ministry.gov
etherpad:
eventbrite: 10000000
----
Other content.
"""
        expected = {
            'startdate': '2015-07-13',
            'enddate': '2015-07-14',
            'country': 'us',
            'venue': 'Euphoric State University',
            'address': 'Highway to Heaven 42, Academipolis',
            'latlng': '36.998977, -109.045173',
            'language': 'us',
            'instructor': 'Hermione Granger, Ron Weasley',
            'helper': 'Peter Parker, Tony Stark, Natasha Romanova',
            'contact': 'hermione@granger.co.uk, rweasley@ministry.gov',
            'eventbrite': '10000000',
        }
        self.assertEqual(expected, find_tags_on_event_index(content))

    def test_finding_tags_on_website(self):
        content = """
<html><head>
<meta name="slug" content="2015-07-13-test" />
<meta name="startdate" content="2015-07-13" />
<meta name="enddate" content="2015-07-14" />
<meta name="country" content="us" />
<meta name="venue" content="Euphoric State University" />
<meta name="address" content="Highway to Heaven 42, Academipolis" />
<meta name="latlng" content="36.998977, -109.045173" />
<meta name="language" content="us" />
<meta name="invalid" content="invalid" />
<meta name="instructor" content="Hermione Granger|Ron Weasley" />
<meta name="helper" content="Peter Parker|Tony Stark|Natasha Romanova" />
<meta name="contact" content="hermione@granger.co.uk, rweasley@ministry.gov" />
<meta name="eventbrite" content="10000000" />
<meta name="charset" content="utf-8" />
</head>
<body>
<h1>test</h1>
</body></html>
"""
        expected = {
            'slug': '2015-07-13-test',
            'startdate': '2015-07-13',
            'enddate': '2015-07-14',
            'country': 'us',
            'venue': 'Euphoric State University',
            'address': 'Highway to Heaven 42, Academipolis',
            'latlng': '36.998977, -109.045173',
            'language': 'us',
            'instructor': 'Hermione Granger|Ron Weasley',
            'helper': 'Peter Parker|Tony Stark|Natasha Romanova',
            'contact': 'hermione@granger.co.uk, rweasley@ministry.gov',
            'eventbrite': '10000000',
        }

        self.assertEqual(expected, find_tags_on_event_website(content))

    def test_parsing_empty_tags(self):
        empty_dict = {}
        expected = {
            'slug': '',
            'language': '',
            'start': None,
            'end': None,
            'country': '',
            'venue': '',
            'address': '',
            'latitude': None,
            'longitude': None,
            'reg_key': None,
            'instructors': [],
            'helpers': [],
            'contact': '',
        }
        self.assertEqual(expected, parse_tags_from_event_website(empty_dict))

    def test_parsing_correct_tags(self):
        tags = {
            'slug': '2015-07-13-test',
            'startdate': '2015-07-13',
            'enddate': '2015-07-14',
            'country': 'us',
            'venue': 'Euphoric State University',
            'address': 'Highway to Heaven 42, Academipolis',
            'latlng': '36.998977, -109.045173',
            'language': 'us',
            'instructor': 'Hermione Granger|Ron Weasley',
            'helper': 'Peter Parker|Tony Stark|Natasha Romanova',
            'contact': 'hermione@granger.co.uk, rweasley@ministry.gov',
            'eventbrite': '10000000',
        }
        expected = {
            'slug': '2015-07-13-test',
            'language': 'US',
            'start': datetime.date(2015, 7, 13),
            'end': datetime.date(2015, 7, 14),
            'country': 'US',
            'venue': 'Euphoric State University',
            'address': 'Highway to Heaven 42, Academipolis',
            'latitude': 36.998977,
            'longitude': -109.045173,
            'reg_key': 10000000,
            'instructors': ['Hermione Granger', 'Ron Weasley'],
            'helpers': ['Peter Parker', 'Tony Stark', 'Natasha Romanova'],
            'contact': 'hermione@granger.co.uk, rweasley@ministry.gov',
        }
        self.assertEqual(expected, parse_tags_from_event_website(tags))

    def test_parsing_tricky_country_language(self):
        """Ensure we always get a 2-char string or nothing."""
        tests = [
            (('Usa', 'English'), ('US', 'EN')),
            (('U', 'E'), ('', '')),
            (('', ''), ('', '')),
        ]
        expected = {
            'slug': '',
            'language': '',
            'start': None,
            'end': None,
            'country': '',
            'venue': '',
            'address': '',
            'latitude': None,
            'longitude': None,
            'reg_key': None,
            'instructors': [],
            'helpers': [],
            'contact': '',
        }

        for (country, language), (country_exp, language_exp) in tests:
            with self.subTest(iso_31661=(country, language)):
                tags = dict(country=country, language=language)
                expected['country'] = country_exp
                expected['language'] = language_exp
                self.assertEqual(expected, parse_tags_from_event_website(tags))

    def test_parsing_tricky_dates(self):
        """Test if non-dates don't get parsed."""
        tests = [
            (('wrong start date', 'wrong end date'), (None, None)),
            (('11/19/2015', '11/19/2015'), (None, None)),
        ]
        expected = {
            'slug': '',
            'language': '',
            'start': None,
            'end': None,
            'country': '',
            'venue': '',
            'address': '',
            'latitude': None,
            'longitude': None,
            'reg_key': None,
            'instructors': [],
            'helpers': [],
            'contact': '',
        }

        for (startdate, enddate), (start, end) in tests:
            with self.subTest(dates=(startdate, enddate)):
                tags = dict(startdate=startdate, enddate=enddate)
                expected['start'] = start
                expected['end'] = end
                self.assertEqual(expected, parse_tags_from_event_website(tags))

    def test_parsing_tricky_list_of_names(self):
        """Ensure we always get a list."""
        tests = [
            (('', ''), ([], [])),
            (('Hermione Granger', 'Peter Parker'),
             (['Hermione Granger'], ['Peter Parker'])),
            (('Harry,Ron', 'Hermione,Ginny'),
             (['Harry,Ron'], ['Hermione,Ginny'])),
            (('Harry| Ron', 'Hermione |Ginny'),
             (['Harry', 'Ron'], ['Hermione', 'Ginny'])),
        ]
        expected = {
            'slug': '',
            'language': '',
            'start': None,
            'end': None,
            'country': '',
            'venue': '',
            'address': '',
            'latitude': None,
            'longitude': None,
            'reg_key': None,
            'instructors': [],
            'helpers': [],
            'contact': '',
        }

        for (instructor, helper), (instructors, helpers) in tests:
            with self.subTest(people=(instructor, helper)):
                tags = dict(instructor=instructor, helper=helper)
                expected['instructors'] = instructors
                expected['helpers'] = helpers
                self.assertEqual(expected, parse_tags_from_event_website(tags))

    def test_parsing_tricky_latitude_longitude(self):
        tests = [
            ('XYZ', (None, None)),
            ('XYZ, ', (None, None)),
            (',-123', (None, -123.0)),
            (',', (None, None)),
            (None, (None, None)),
        ]
        expected = {
            'slug': '',
            'language': '',
            'start': None,
            'end': None,
            'country': '',
            'venue': '',
            'address': '',
            'latitude': None,
            'longitude': None,
            'reg_key': None,
            'instructors': [],
            'helpers': [],
            'contact': '',
        }
        for latlng, (latitude, longitude) in tests:
            with self.subTest(latlng=latlng):
                tags = dict(latlng=latlng)
                expected['latitude'] = latitude
                expected['longitude'] = longitude
                self.assertEqual(expected, parse_tags_from_event_website(tags))

    def test_parsing_tricky_eventbrite_id(self):
        tests = [
            ('', None),
            ('string', None),
            (None, None),
        ]
        expected = {
            'slug': '',
            'language': '',
            'start': None,
            'end': None,
            'country': '',
            'venue': '',
            'address': '',
            'latitude': None,
            'longitude': None,
            'reg_key': None,
            'instructors': [],
            'helpers': [],
            'contact': '',
        }
        for eventbrite_id, reg_key in tests:
            with self.subTest(eventbrite_id=eventbrite_id):
                tags = dict(eventbrite=eventbrite_id)
                expected['reg_key'] = reg_key
                self.assertEqual(expected, parse_tags_from_event_website(tags))

    def test_validating_invalid_tags(self):
        tags = {
            'slug': 'WRONG FORMAT',
            'language': 'ENGLISH',
            'startdate': '07/13/2015',
            'enddate': '07/14/2015',
            'country': 'USA',
            'venue': 'Euphoric State University',
            'address': 'Highway to Heaven 42, Academipolis',
            'latlng': '3699e-4, -1.09e2',
            'instructor': 'Hermione Granger, Ron Weasley',
            'helper': 'Peter Parker, Tony Stark, Natasha Romanova',
            'contact': 'hermione@granger.co.uk, rweasley@ministry.gov',
            'eventbrite': 'bigmoney',
        }
        errors = validate_tags_from_event_website(tags)
        assert len(errors) == 7
        assert all([error.startswith('Invalid value') for error in errors])

    def test_validating_missing_tags(self):
        tags = {}
        errors = validate_tags_from_event_website(tags)
        assert len(errors) == 12
        assert all([error.startswith('Missing') for error in errors])

    def test_validating_default_tags(self):
        tags = {
            'slug': 'FIXME',
            'language': 'FIXME',
            'startdate': 'FIXME',
            'enddate': 'FIXME',
            'country': 'FIXME',
            'venue': 'FIXME',
            'address': 'FIXME',
            'latlng': 'FIXME',
            'eventbrite': 'FIXME',
            'instructor': 'FIXME',
            'helper': 'FIXME',
            'contact': 'FIXME',
        }
        errors = validate_tags_from_event_website(tags)
        assert len(errors) == 12
        assert all([
            error.startswith('Placeholder value "FIXME"')
            for error in errors
        ])

    def test_validating_correct_tags(self):
        tags = {
            'slug': '2015-07-13-test',
            'language': 'us',
            'startdate': '2015-07-13',
            'enddate': '2015-07-14',
            'country': 'us',
            'venue': 'Euphoric State University',
            'address': 'Highway to Heaven 42, Academipolis',
            'latlng': '36.998977, -109.045173',
            'eventbrite': '10000000',
            'instructor': 'Hermione Granger, Ron Weasley',
            'helper': 'Peter Parker, Tony Stark, Natasha Romanova',
            'contact': 'hermione@granger.co.uk, rweasley@ministry.gov',
        }
        errors = validate_tags_from_event_website(tags)
        assert not errors


class TestMembership(TestBase):
    """Tests for SCF membership."""

    def setUp(self):
        super().setUp()
        self._setUpUsersAndLogin()

        one_day = datetime.timedelta(days=1)
        one_month = datetime.timedelta(days=30)
        three_years = datetime.timedelta(days=3 * 365)

        today = datetime.date.today()
        yesterday = today - one_day
        tomorrow = today + one_day

        # Set up events in the past, at present, and in future.
        past = Event.objects.create(
            host=self.host_alpha,
            slug="in-past",
            start=today - three_years,
            end=tomorrow - three_years
        )

        present = Event.objects.create(
            host=self.host_alpha,
            slug="at-present",
            start=today - one_month
        )

        future = Event.objects.create(
            host=self.host_alpha,
            slug="in-future",
            start=today + one_month,
            end=tomorrow + one_month
        )

        # Roles and badges.
        instructor_role = Role.objects.create(name='instructor')
        member_badge = Badge.objects.create(name='member')

        # Spiderman is an explicit member.
        Award.objects.create(person=self.spiderman, badge=member_badge,
                             awarded=yesterday)

        # Hermione teaches in the past, now, and in future, so she's a member.
        Task.objects.create(event=past, person=self.hermione,
                            role=instructor_role)
        Task.objects.create(event=present, person=self.hermione,
                            role=instructor_role)
        Task.objects.create(event=future, person=self.hermione,
                            role=instructor_role)

        # Ron only teaches in the distant past, so he's not a member.
        Task.objects.create(event=past, person=self.ron,
                            role=instructor_role)

        # Harry only teaches in the future, so he's not a member.
        Task.objects.create(event=future, person=self.harry,
                            role=instructor_role)

    def test_members_default_cutoffs(self):
        """Make sure default membership rules are obeyed."""
        earliest, latest = default_membership_cutoff()
        members = get_members(earliest=earliest, latest=latest)

        self.assertIn(self.hermione, members)  # taught recently
        self.assertNotIn(self.ron, members)  # taught too long ago
        self.assertNotIn(self.harry, members)  # only teaching in the future
        self.assertIn(self.spiderman, members)  # explicit member
        self.assertEqual(len(members), 2)

    def test_members_explicit_earliest(self):
        """Make sure membership rules are obeyed with explicit earliest
        date."""
        # Set start date to exclude Hermione.
        earliest = datetime.date.today() - datetime.timedelta(days=1)
        _, latest = default_membership_cutoff()
        members = get_members(earliest=earliest, latest=latest)

        self.assertNotIn(self.hermione, members)  # taught recently
        self.assertNotIn(self.ron, members)  # taught too long ago
        self.assertNotIn(self.harry, members)  # only teaching in the future
        self.assertIn(self.spiderman, members)  # explicit member
        self.assertEqual(len(members), 1)


class TestAssignmentSelection(TestCase):
    def setUp(self):
        """Set up RequestFactory and some users with different levels of
        privileges."""
        self.factory = RequestFactory()
        self.superuser = Person.objects.create_superuser(
            username='superuser', personal='admin', family='admin',
            email='superuser@superuser', password='superuser')
        self.admin = Person.objects.create_user(
            username='admin', personal='admin', family='admin',
            email='admin@admin', password='admin')
        self.admin.groups = [Group.objects.get(name='administrators')]
        self.normal_user = Person.objects.create_user(
            username='user', personal='typical', family='user',
            email='typical@user', password='user')

    def test_no_selection_superuser(self):
        """User is superuser and they selected nothing. The result should be
        default value for this kind of a user."""
        request = self.factory.get('/')
        request.user = self.superuser
        assignment, is_admin = assignment_selection(request)
        self.assertEqual(assignment, 'all')
        self.assertFalse(is_admin)

    def test_no_selection_admin(self):
        """User is admin and they selected nothing. The result should be
        default value for this kind of a user."""
        request = self.factory.get('/')
        request.user = self.admin
        assignment, is_admin = assignment_selection(request)
        self.assertEqual(assignment, 'me')
        self.assertTrue(is_admin)

    def test_no_selection_normal_user(self):
        """User is normal user and they selected nothing. The result should be
        default value for this kind of a user."""
        request = self.factory.get('/')
        request.user = self.normal_user
        assignment, is_admin = assignment_selection(request)
        self.assertEqual(assignment, 'all')
        self.assertFalse(is_admin)

    def test_selection_normal_user(self):
        """User is normal user and they selected self-assigned. This is invalid
        selection (normal user cannot select anything), so the default option
        should be returned."""
        request = self.factory.get('/', {'assigned_to': 'me'})
        request.user = self.normal_user
        assignment, is_admin = assignment_selection(request)
        self.assertEqual(assignment, 'all')
        self.assertFalse(is_admin)

    def test_selection_privileged_user(self):
        """User is admin and they selected "not assigned to anyone". Actually
        for privileged user any selection should make through."""
        request = self.factory.get('/', {'assigned_to': 'noone'})
        request.user = self.admin
        assignment, is_admin = assignment_selection(request)
        self.assertEqual(assignment, 'noone')
        self.assertTrue(is_admin)
