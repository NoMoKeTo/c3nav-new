# -*- coding: utf-8 -*-
# Generated by Django 1.11.2 on 2017-07-08 15:52
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mapdata', '0017_point_to_poi'),
    ]

    operations = [
        migrations.AlterField(
            model_name='locationslug',
            name='slug',
            field=models.SlugField(blank=True, null=True, unique=True, verbose_name='Slug'),
        ),
    ]