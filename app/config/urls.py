from django.contrib import admin
from django.contrib.auth import views as auth
from django.urls import path

from tickets import views, views_backup, views_import
from tickets.signin import SignInForm

handler403 = "tickets.views.error_403"
handler404 = "tickets.views.error_404"

urlpatterns = [
    path("", views.home, name="home"),
    path("sw.js", views.service_worker, name="service_worker"),
    path("tickets/", views.ticket_list, name="ticket_list"),
    path("tickets/new/", views.ticket_new, name="ticket_new"),
    path("tickets/bulk/", views.ticket_bulk, name="ticket_bulk"),
    path("tickets/board/", views.board, name="board"),
    path("tickets/export.csv", views.export_csv, name="export_csv"),
    path("tickets/<str:number>/", views.ticket_detail, name="ticket_detail"),
    path("tickets/<str:number>/<str:action>/", views.ticket_action, name="ticket_action"),
    path("files/<int:pk>/", views.attachment, name="attachment"),
    path("equipment/", views.equipment_list, name="equipment_list"),
    path("equipment/<int:pk>/", views.equipment_detail, name="equipment_detail"),
    path("equipment/<int:pk>/pm/new/", views.equipment_pm_create, name="equipment_pm_create"),
    path("equipment/pm/<int:pk>/ticket/", views.pm_trigger_ticket, name="pm_trigger_ticket"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("alerts/", views.notifications, name="notifications"),
    path("alerts/<int:pk>/", views.notification_open, name="notification_open"),
    path("settings/", views.settings_view, name="settings"),
    path("settings/templates/", views.message_templates, name="message_templates"),
    path("settings/import/", views_import.import_page, name="import_page"),
    path("settings/import/workbook.xlsx", views_import.import_workbook, name="import_workbook"),
    path("settings/backups/", views_backup.backups_view, name="backups"),
    path("settings/backups/action/", views_backup.backups_action, name="backups_action"),
    path("settings/backups/jobs/<str:job_id>.json", views_backup.backup_job, name="backup_job"),
    path("centres/", views.centres, name="centres"),
    path("masters/", views.masters_hub, name="masters_hub"),
    path("masters/<str:section>/action/", views.masters_action, name="masters_action"),
    path("reports/", views.reports_view, name="reports"),
    path("reports/excel/", views.report_excel, name="report_excel"),
    path("reports/print/", views.report_print, name="report_print"),
    path("equipment/labels/", views.equipment_labels, name="equipment_labels"),
    path("equipment/<int:pk>/qr.svg", views.equipment_qr, name="equipment_qr"),
    path("me/", views.profile, name="profile"),
    path("me/sessions/clear/", views.invalidate_sessions_view, name="invalidate_sessions"),
    path("brand/logo", views.brand_logo, name="brand_logo"),
    path("brand/logo-wide", views.brand_logo_wide, name="brand_logo_wide"),
    path("people/<int:pk>/photo", views.person_photo, name="person_photo"),
    path("api/live/", views.api_live, name="api_live"),
    path("api/live/stream/", views.api_live_stream, name="api_live_stream"),
    path("metrics", views.metrics_view, name="metrics"),
    path("api/search/", views.api_search, name="api_search"),
    path("api/duplicates/", views.api_duplicates, name="api_duplicates"),
    path("api/people/", views.api_people, name="api_people"),
    path("healthz", views.healthz),

    path("login/", auth.LoginView.as_view(redirect_authenticated_user=True, authentication_form=SignInForm), name="login"),
    path("logout/", auth.LogoutView.as_view(), name="logout"),
    path("password/", views.PasswordChangeView.as_view(success_url="/password/done/"), name="password_change"),
    path("password/done/", auth.PasswordChangeDoneView.as_view(), name="password_change_done"),
    path("password/forgot/", views.PasswordResetView.as_view(), name="password_reset"),
    path("password/forgot/sent/", auth.PasswordResetDoneView.as_view(), name="password_reset_done"),
    path("password/reset/<uidb64>/<token>/", views.PasswordResetConfirmView.as_view(), name="password_reset_confirm"),
    path("password/reset/done/", auth.PasswordResetCompleteView.as_view(), name="password_reset_complete"),

    path("admin/", admin.site.urls),
]
