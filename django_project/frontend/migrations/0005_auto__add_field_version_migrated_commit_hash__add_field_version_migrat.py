# -*- coding: utf-8 -*-
from south.utils import datetime_utils as datetime
from south.db import db
from south.v2 import SchemaMigration
from django.db import models


class Migration(SchemaMigration):

    def forwards(self, orm):
        # Adding field 'Version.migrated_commit_hash'
        db.add_column('version', 'migrated_commit_hash',
                      self.gf('django.db.models.fields.CharField')(default=None, max_length=255, null=True),
                      keep_default=False)

        # Adding field 'Version.migrated_version_id'
        db.add_column('version', 'migrated_version_id',
                      self.gf('django.db.models.fields.PositiveIntegerField')(default=None, null=True),
                      keep_default=False)


    def backwards(self, orm):
        # Deleting field 'Version.migrated_commit_hash'
        db.delete_column('version', 'migrated_commit_hash')

        # Deleting field 'Version.migrated_version_id'
        db.delete_column('version', 'migrated_version_id')


    models = {
        u'frontend.article': {
            'Meta': {'object_name': 'Article', 'db_table': "'Articles'"},
            'git_dir': ('django.db.models.fields.CharField', [], {'max_length': '4096'}),
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'initial_date': ('django.db.models.fields.DateTimeField', [], {'default': 'datetime.datetime.utcnow'}),
            'is_migrated': ('django.db.models.fields.BooleanField', [], {'default': 'False'}),
            'last_check': ('django.db.models.fields.DateTimeField', [], {'null': 'True'}),
            'last_update': ('django.db.models.fields.DateTimeField', [], {'null': 'True'}),
            'url': ('django.db.models.fields.CharField', [], {'unique': 'True', 'max_length': '255', 'db_index': 'True'})
        },
        u'frontend.upvote': {
            'Meta': {'object_name': 'Upvote', 'db_table': "'upvotes'"},
            'article_id': ('django.db.models.fields.IntegerField', [], {}),
            'creation_time': ('django.db.models.fields.DateTimeField', [], {}),
            'diff_v1': ('django.db.models.fields.CharField', [], {'max_length': '255'}),
            'diff_v2': ('django.db.models.fields.CharField', [], {'max_length': '255'}),
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'upvoter_ip': ('django.db.models.fields.CharField', [], {'max_length': '255'})
        },
        u'frontend.version': {
            'Meta': {'object_name': 'Version', 'db_table': "'version'"},
            'article': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['frontend.Article']"}),
            'boring': ('django.db.models.fields.BooleanField', [], {'default': 'False'}),
            'byline': ('django.db.models.fields.CharField', [], {'max_length': '255'}),
            'date': ('django.db.models.fields.DateTimeField', [], {}),
            'diff_json': ('django.db.models.fields.CharField', [], {'max_length': '255', 'null': 'True'}),
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'is_migrated': ('django.db.models.fields.BooleanField', [], {'default': 'False'}),
            'migrated_commit_hash': ('django.db.models.fields.CharField', [], {'default': 'None', 'max_length': '255', 'null': 'True'}),
            'migrated_version_id': ('django.db.models.fields.PositiveIntegerField', [], {'default': 'None', 'null': 'True'}),
            'title': ('django.db.models.fields.CharField', [], {'max_length': '255'}),
            'v': ('django.db.models.fields.CharField', [], {'unique': 'True', 'max_length': '255'})
        }
    }

    complete_apps = ['frontend']