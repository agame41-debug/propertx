from report.routes import admin, audit_routes, auth, dashboard, operations, property_routes, reconciliation, sources, logs


def register_all(app, state) -> None:
    auth.register(app, state)
    dashboard.register(app, state)
    sources.register(app, state)
    property_routes.register(app, state)
    operations.register(app, state)
    reconciliation.register(app, state)
    audit_routes.register(app, state)
    logs.register(app, state)
    admin.register(app, state)
