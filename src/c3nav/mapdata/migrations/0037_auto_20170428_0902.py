# -*- coding: utf-8 -*-
# Generated by Django 1.10.4 on 2017-04-28 09:02
from __future__ import unicode_literals

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mapdata', '0036_arealocation_bssids'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='escalatorslope',
            name='level',
        ),
        migrations.RemoveField(
            model_name='escalatorslope',
            name='package',
        ),
        migrations.RemoveField(
            model_name='oneway',
            name='level',
        ),
        migrations.RemoveField(
            model_name='oneway',
            name='package',
        ),
        migrations.AlterField(
            model_name='arealocation',
            name='bssids',
            field=models.TextField(blank=True, validators=[django.core.validators.RegexValidator(message='please enter a newline seperated lowercase list of BSSIDs', regex='^([0-9a-f]{2}(:[0-9a-f]{2}){5}(\\r?\\n[0-9a-f]{2}(:[0-9a-f]{2}){5})*)?$')], verbose_name='BSSIDs'),
        ),
        migrations.DeleteModel(
            name='EscalatorSlope',
        ),
        migrations.DeleteModel(
            name='OneWay',
        ),
    ]
