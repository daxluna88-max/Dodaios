"""The phone companion's server side: paired devices and resumable file transfers.

``devices`` decides who may call ``/api/phone/*``; ``store`` keeps the files. Neither knows HTTP:
``dodaios/ui/web/phone_routes.py`` is the only adapter.
"""
