from django.contrib import admin

from infrastructure.oauth_github.models import GitHubAccount

admin.site.register(GitHubAccount)
