import logging
import discord
from discord import app_commands
from discord.ext import commands

from utils.vip_manager import vip_manager, require_bot_admin

logger = logging.getLogger("bot.admin")

# --- Interactive Modals for Admin Dashboard ---

class UserIDModal(discord.ui.Modal):
    def __init__(self, title: str, label: str, mode: str):
        super().__init__(title=title)
        self.mode = mode  # "bot_admin_add", "bot_admin_remove", "vip_add", "vip_remove"
        self.user_id_input = discord.ui.TextInput(
            label=label,
            placeholder="請輸入成員的 18 位數 Discord User ID (例: 123456789012345678)",
            min_length=15,
            max_length=20,
            required=True
        )
        self.add_item(self.user_id_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            target_id = int(self.user_id_input.value.strip())
        except ValueError:
            await interaction.response.send_message("❌ 請輸入有效的數字 Discord User ID！", ephemeral=True)
            return

        if self.mode == "bot_admin_add":
            added = vip_manager.add_bot_admin(target_id)
            msg = f"👑 已成功新增成員 <@{target_id}> (`{target_id}`) 為 Bot 管理員！" if added else f"ℹ️ 成員 <@{target_id}> 已經是 Bot 管理員了！"
        elif self.mode == "bot_admin_remove":
            removed = vip_manager.remove_bot_admin(target_id)
            msg = f"🧹 已成功將成員 <@{target_id}> (`{target_id}`) 從 Bot 管理員名單移除！" if removed else f"ℹ️ 成員 <@{target_id}> 不在 Bot 管理員名單中！"
        elif self.mode == "vip_add":
            added = vip_manager.add_vip(target_id)
            msg = f"💎 已成功將成員 <@{target_id}> (`{target_id}`) 新增至 VIP 白名單！" if added else f"ℹ️ 成員 <@{target_id}> 已經在 VIP 白名單中了！"
        elif self.mode == "vip_remove":
            removed = vip_manager.remove_vip(target_id)
            msg = f"🧹 已成功將成員 <@{target_id}> (`{target_id}`) 從 VIP 白名單移除！" if removed else f"ℹ️ 成員 <@{target_id}> 不在 VIP 白名單中！"
        else:
            msg = "未知操作"

        await interaction.response.send_message(msg, ephemeral=True)

class SetLimitModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="📊 設定非 VIP 每日對話上限")
        self.limit_input = discord.ui.TextInput(
            label="每日對話上限次數",
            placeholder="請輸入數字 (例: 20)",
            min_length=1,
            max_length=5,
            required=True
        )
        self.add_item(self.limit_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            limit_val = int(self.limit_input.value.strip())
            if limit_val < 1:
                raise ValueError
        except ValueError:
            await interaction.response.send_message("❌ 請輸入大於 0 的有效數字！", ephemeral=True)
            return

        vip_manager.set_daily_limit(limit_val)
        await interaction.response.send_message(f"✅ 已成功將非 VIP 每日對話上限調整為：**{limit_val}** 次！", ephemeral=True)

# --- Interactive Control Panel View ---

class AdminDashboardView(discord.ui.View):
    def __init__(self, cog: "AdminCog"):
        super().__init__(timeout=180)
        self.cog = cog

    @discord.ui.button(label="👑 新增 Bot 管理員", style=discord.ButtonStyle.primary, row=0)
    async def add_bot_admin_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(UserIDModal(title="👑 新增 Bot 管理員", label="請輸入要新增的管理員 User ID", mode="bot_admin_add"))

    @discord.ui.button(label="🧹 移除 Bot 管理員", style=discord.ButtonStyle.danger, row=0)
    async def remove_bot_admin_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(UserIDModal(title="🧹 移除 Bot 管理員", label="請輸入要移除的管理員 User ID", mode="bot_admin_remove"))

    @discord.ui.button(label="💎 新增 VIP 白名單", style=discord.ButtonStyle.success, row=1)
    async def add_vip_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(UserIDModal(title="💎 新增 VIP 白名單", label="請輸入要新增的 VIP User ID", mode="vip_add"))

    @discord.ui.button(label="🧹 移除 VIP 白名單", style=discord.ButtonStyle.danger, row=1)
    async def remove_vip_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(UserIDModal(title="🧹 移除 VIP 白名單", label="請輸入要移除的 VIP User ID", mode="vip_remove"))

    @discord.ui.button(label="📊 設定每日對話上限", style=discord.ButtonStyle.secondary, row=2)
    async def set_limit_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(SetLimitModal())

    @discord.ui.button(label="📋 查看狀態總覽", style=discord.ButtonStyle.secondary, row=2)
    async def view_status_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        admin_ids = vip_manager.config.get("admin_user_ids", [])
        vip_ids = vip_manager.config.get("vip_user_ids", [])
        limit = vip_manager.config.get("daily_limit_non_vip", 10)
        enabled = vip_manager.config.get("enabled", True)

        status_str = "🟢 已啟用限制" if enabled else "🔴 已停用限制 (全員無限)"
        admin_mentions = [f"<@{uid}> (`{uid}`)" for uid in admin_ids] if admin_ids else ["未設定"]
        vip_mentions = [f"<@{uid}> (`{uid}`)" for uid in vip_ids] if vip_ids else ["無 VIP 成員"]

        embed = discord.Embed(title="⚙️ Bot 控制面板 - 當前狀態總覽", color=discord.Color.gold())
        embed.add_field(name="👑 Bot 超級管理員 User ID", value="\n".join(admin_mentions), inline=False)
        embed.add_field(name="⚙️ 對話限制狀態", value=status_str, inline=False)
        embed.add_field(name="📊 非 VIP 每日對話上限", value=f"**{limit}** 次 / 天", inline=False)
        embed.add_field(name="💎 VIP 無限額度名單", value="\n".join(vip_mentions), inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

# --- AdminCog ---

class AdminCog(commands.Cog):
    """Cog for Bot Admin & VIP Management commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        """Automatically add Bot Application Owner ID to Bot Admin Whitelist on startup."""
        try:
            app_info = await self.bot.application_info()
            owner_id = app_info.owner.id
            added = vip_manager.add_bot_admin(owner_id)
            if added:
                logger.info(f"👑 [Auto Setup] Successfully bootstrapped Bot Application Owner ({owner_id}) to Bot Admin Whitelist!")
        except Exception as e:
            logger.warning(f"Could not bootstrap Bot owner ID: {e}")

    # --- Interactive Admin Control Panel Slash Command ---

    @app_commands.command(name="admin_panel", description="開啟 Bot 專屬管理控制面板 (僅限白名單 User ID)")
    async def admin_panel(self, interaction: discord.Interaction):
        # Strict Bot Admin User ID Check
        if not vip_manager.is_bot_admin(interaction.user.id):
            await interaction.response.send_message(
                f"⛔ **權限被拒**！您的 Discord User ID (`{interaction.user.id}`) 不在 Bot 管理員白名單中，無法使用控制面板。",
                ephemeral=True
            )
            return

        embed = discord.Embed(
            title="🎛️ Bot 超級管理控制面板 (Admin Panel)",
            description=f"歡迎，Bot 管理員 {interaction.user.mention}！\n請點擊下方按鈕進行 VIP 白名單、管理員名單與對話上限設定：",
            color=discord.Color.blue()
        )
        embed.set_footer(text="提示：本控制面板訊息為專屬私人顯示 (Ephemeral)，其他人看不到！")
        view = AdminDashboardView(self)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="vip_list", description="檢視 Bot 管理員名單、VIP 白名單與每日對話限制設定")
    async def vip_list(self, interaction: discord.Interaction):
        admin_ids = vip_manager.config.get("admin_user_ids", [])
        vip_ids = vip_manager.config.get("vip_user_ids", [])
        limit = vip_manager.config.get("daily_limit_non_vip", 10)
        enabled = vip_manager.config.get("enabled", True)

        status_str = "🟢 已啟用限制" if enabled else "🔴 已停用限制 (全員無限)"
        admin_mentions = [f"<@{uid}> (`{uid}`)" for uid in admin_ids] if admin_ids else ["未設定 (將自動將 Bot 擁有者作為首位管理員)"]
        vip_mentions = [f"<@{uid}> (`{uid}`)" for uid in vip_ids] if vip_ids else ["目前無 VIP 成員"]

        embed = discord.Embed(title="⚙️ Bot 獨立管理層與 VIP 設定狀態", color=discord.Color.gold())
        embed.add_field(name="👑 Bot 超級管理員 User ID", value="\n".join(admin_mentions), inline=False)
        embed.add_field(name="⚙️ 對話限制狀態", value=status_str, inline=False)
        embed.add_field(name="📊 非 VIP 每日對話上限", value=f"**{limit}** 次 / 天", inline=False)
        embed.add_field(name="💎 VIP 無限額度名單", value="\n".join(vip_mentions), inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=False)

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        """Handle permission errors for admin slash commands."""
        err_msg = str(error)
        if "NOT_BOT_ADMIN" in err_msg or isinstance(error, app_commands.MissingPermissions):
            msg = "⛔ **權限被拒**！此管理指令僅限指定的 Bot 管理員 (User ID) 執行，伺服器管理員身分無效。"
            if not interaction.response.is_done():
                await interaction.response.send_message(msg, ephemeral=True)
            else:
                await interaction.followup.send(msg, ephemeral=True)
        else:
            logger.error(f"App command error in AdminCog: {error}", exc_info=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCog(bot))
