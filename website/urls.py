from django.conf.urls import include, url

import settings

urlpatterns = []

if settings.DEBUG:
    import logging
    import os

    from django.views.generic import RedirectView

    import util.path

    logger = logging.getLogger(__name__)
    document_root = util.path.prepend_project_dir(os.path.pardir, 'static')
    logger.info('document_root: %s', document_root)

    urlpatterns += [
        # url(r'^static/(?P<path>.*)$', 'django.views.static.serve', {
        #     'document_root': document_root,
        # }, name='static'),
        url(r'^robots.txt$', RedirectView.as_view(url='/static/robots.txt'))
    ]
# else:
#     urlpatterns += [
#         url(r'^static/(?P<path>.*)$', 'django.views.static.serve', {
#             'document_root': document_root,
#         }, name='static'),
#     ]

urlpatterns += [
    url(r'', include('frontend.urls')),
]
