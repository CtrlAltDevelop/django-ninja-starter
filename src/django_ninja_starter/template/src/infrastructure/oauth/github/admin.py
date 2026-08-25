from django.contrib import admin

from infrastructure.oauth.github.models import GitHubAccount

admin.site.register(GitHubAccount)
