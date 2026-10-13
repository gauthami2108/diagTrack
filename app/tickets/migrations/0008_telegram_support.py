# Generated for Telegram support

from django.db import migrations, models
import tickets.crypto


class Migration(migrations.Migration):

    dependencies = [
        ("tickets", "0007_phase2_enhancements"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="telegram_chat_id",
            field=models.CharField(blank=True, help_text="Chat ID for Telegram alerts (send /start to the bot to find it).", max_length=60, verbose_name="Telegram chat ID"),
        ),
        migrations.AlterField(
            model_name="outboundmessage",
            name="channel",
            field=models.CharField(choices=[("email", "Email"), ("sms", "SMS"), ("whatsapp", "WhatsApp"), ("telegram", "Telegram")], max_length=12),
        ),
        migrations.AddField(
            model_name="sitesettings",
            name="telegram_enabled",
            field=models.BooleanField(default=False, verbose_name="Telegram alerts"),
        ),
        migrations.AddField(
            model_name="sitesettings",
            name="telegram_bot_token",
            field=tickets.crypto.EncryptedTextField(blank=True, help_text="From @BotFather, e.g. 123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11", verbose_name="Telegram bot token"),
        ),
        migrations.AddField(
            model_name="sitesettings",
            name="telegram_default_chat_id",
            field=models.CharField(blank=True, help_text="Optional. Group or channel ID (e.g. -100123456789) to receive urgent alerts.", max_length=60, verbose_name="Default Telegram group/channel chat ID"),
        ),
    ]
