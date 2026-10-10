from django.contrib import admin
from django.contrib.auth import views as auth
from django.urls import path

from tickets import views, views_backup

handler403 = "tickets.views.error_403"
handler404 = "tickets.views.error_404"

urlpatterns = [
    path("", views.home, name="home"),
    path("tickets/", views.ticket_list, name="ticket_list"),
    path("tickets/new/", views.ticket_new, name="ticket_new"),
    path("tickets/board/", views.board, name="board"),
    path("tickets/export.csv", views.export_csv, name="export_csv"),
    path("tickets/<str:number>/", views.ticket_detail, name="ticket_detail"),
    path("tickets/<str:number>/<str:action>/", views.ticket_action, name="ticket_action"),
    path("files/<int:pk>/", views.attachment, name="attachment"),
    path("equipment/", views.equipment_list, name="equipment_list"),
    path("equipment/<int:pk>/", views.equipment_detail, name="equipment_detail"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("alerts/", views.notifications, name="notifications"),
    path("alerts/<int:pk>/", views.notification_open, name="notification_open"),
    path("settings/", views.settings_view, name="settings"),
    path("settings/templates/", views.message_templates, name="message_templates"),
    path("settings/backups/", views_backup.backups_view, name="backups"),
    path("settings/backups/action/", views_backup.backups_action, name="backups_action"),
    path("settings/backups/jobs/<str:job_id>.json", views_backup.backup_job, name="backup_job"),
    path("centres/", views.centres, name="centres"),
    path("reports/", views.reports_view, name="reports"),
    path("reports/excel/", views.report_excel, name="report_excel"),
    path("reports/print/", views.report_print, name="report_print"),
    path("equipment/labels/", views.equipment_labels, name="equipment_labels"),
    path("equipment/<int:pk>/qr.svg", views.equipment_qr, name="equipment_qr"),
    path("me/", views.profile, name="profile"),
    path("brand/logo", views.brand_logo, name="brand_logo"),
    path("brand/logo-wide", views.brand_logo_wide, name="brand_logo_wide"),
    path("people/<int:pk>/photo", views.person_photo, name="person_photo"),
    path("api/live/", views.api_live, name="api_live"),
    path("api/search/", views.api_search, name="api_search"),
    path("api/people/", views.api_people, name="api_people"),
    path("healthz", views.healthz),

    path("login/", auth.LoginView.as_view(redirect_authenticated_user=True), name="login"),
    path("logout/", auth.LogoutView.as_view(), name="logout"),
    path("password/", auth.PasswordChangeView.as_view(success_url="/password/done/"), name="password_change"),
    path("password/done/", auth.PasswordChangeDoneView.as_view(), name="password_change_done"),
    path("password/forgot/", views.PasswordResetView.as_view(), name="password_reset"),
    path("password/forgot/sent/", auth.PasswordResetDoneView.as_view(), name="password_reset_done"),
    path("password/reset/<uidb64>/<token>/", auth.PasswordResetConfirmView.as_view(), name="password_reset_confirm"),
    path("password/reset/done/", auth.PasswordResetCompleteView.as_view(), name="password_reset_complete"),

    path("admin/", admin.site.urls),
]
