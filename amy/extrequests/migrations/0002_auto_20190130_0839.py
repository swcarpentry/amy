# Generated by Django 2.1.2 on 2019-01-30 08:39

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('extrequests', '0001_initial'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='profileupdaterequest',
            name='domains',
        ),
        migrations.RemoveField(
            model_name='profileupdaterequest',
            name='languages',
        ),
        migrations.RemoveField(
            model_name='profileupdaterequest',
            name='lessons',
        ),
        migrations.DeleteModel(
            name='ProfileUpdateRequest',
        ),
    ]
