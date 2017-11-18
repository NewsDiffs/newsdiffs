from django.conf.urls import include, url

import settings

urlpatterns = []

if settings.DEBUG:

    from django.views.generic import RedirectView

    urlpatterns += [
        url(r'^robots.txt$', RedirectView.as_view(url='/static/robots.txt'))
    ]

urlpatterns += [
    url(r'', include('frontend.urls')),
]
