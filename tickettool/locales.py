# Per-server localization strings for TicketTool
# Supports: English (en) and Portuguese-Brazilian (pt-br)

STRINGS = {
    "en": {
        # Button labels
        "create_ticket": "Create ticket",
        "close": "Close",
        "re_open": "Re-open",
        "claim": "Claim",
        "delete": "Delete",

        # Dropdown
        "choose_reason": "Choose the reason for open a ticket.",

        # Ticket actions
        "action_taken": "Action taken for the ticket system.",
        "ticket_action_title": "Ticket [{profile}] {id} - Action taken",

        # Embed fields
        "ticket_id": "Ticket ID:",
        "owned_by": "Owned by:",
        "channel": "Channel:",
        "closed_by": "Closed by:",
        "deleted_by": "Deleted by:",
        "closed_at": "Closed at:",
        "reason": "Reason:",

        # Ticket status messages
        "ticket_created": "Ticket Created",
        "ticket_created_thanks": "Thank you for creating a ticket on this server!",
        "ticket_created_by": "The ticket was created by {created_by}.",
        "report_creation": "Report on the creation of the ticket {id}.",

        "ticket_opened": "Ticket Opened",
        "ticket_opened_by": "The ticket was opened by {opened_by}.",
        "report_close": "Report on the close of the ticket {id}.",

        "ticket_closed": "Ticket Closed",
        "ticket_closed_by": "The ticket was closed by {closed_by}.",

        "ticket_locked": "Ticket Locked",
        "ticket_locked_by": "The ticket was locked by {locked_by}.",
        "report_lock": "Report on the lock of the ticket {id}.",

        "ticket_unlocked": "Ticket Unlocked",
        "ticket_unlocked_by": "The ticket was unlocked by {unlocked_by}.",
        "report_unlock": "Report on the unlock of the ticket {id}.",

        "ticket_renamed": "Ticket Renamed.",

        "ticket_deleted": "Ticket Deleted",
        "ticket_deleted_by": "The ticket was deleted by {deleted_by}.",
        "report_deletion": "Report on the deletion of the ticket {id}.",

        "ticket_claimed": "Ticket claimed.",
        "ticket_unclaimed": "Ticket unclaimed.",
        "owner_modified": "Owner Modified.",

        # Audit reasons
        "creating_ticket": "Creating the ticket {id}.",
        "opening_ticket": "Opening the ticket {id}.",
        "closing_ticket": "Closing the ticket {id}.",
        "locking_ticket": "Locking the ticket {id}.",
        "unlocking_ticket": "Unlocking the ticket {id}.",
        "renaming_ticket": "Renaming the ticket {id}. (`{old_name}` to `{new_name}`)",
        "deleting_ticket": "Deleting the ticket {id}.",
        "claiming_ticket": "Claiming the ticket {id}.",
        "unclaiming_ticket": "Unclaiming the ticket {id}.",
        "changing_owner": "Changing owner of the ticket {id}.",
        "adding_member": "Adding a member to the ticket {id}.",
        "removing_member": "Removing a member to the ticket {id}.",

        # Channel topic
        "channel_topic": "🎟️ Ticket ID: {id}\n🕵️ Ticket created by: @{created_by_name} ({created_by_id})\n☢️ Ticket reason: {short_reason}\n",

        # Error messages
        "profile_not_exist": "This profile does not exist.",
        "not_in_ticket": "You're not in a ticket.",
        "ticket_not_status": "This ticket isn't {status}ed.",
        "ticket_is_status": "This ticket is {status}.",
        "not_allowed_lock": "You're not allowed to lock this ticket.",
        "not_allowed_view": "You're not allowed to view this ticket.",
        "provide_profile": "Please provide a profile.",
        "no_profile_created": "No profile has been created on this server.",
        "system_not_enabled": "The ticket system is not enabled on this server. Please ask an administrator of this server to use the `{prefix}settickettool` subcommands to configure it.",
        "category_not_configured": "The category `open` or the category `close` have not been configured. Please ask an administrator of this server to use the `{prefix}settickettool` subcommands to configure it.",
        "limit_reached": "Sorry. You have already reached the limit of {limit} open tickets.",
        "no_manage_channels": "The bot does not have `manage_channels` permission on the `open` and `close` categories to allow the ticket system to function properly. Please notify an administrator of this server.",
        "no_manage_forum": "The bot does not have `manage_channel` permission in the forum channel to allow the ticket system to function properly. Please notify an administrator of this server.",
        "dynamic_name_error": "The dynamic channel name does not contain correct variable names and must be re-configured with `[p]settickettool dynamicchannelname`.",
        "thread_add_error": "⚠ At least one user (the ticket owner or a team member) could not be added to the ticket thread. Maybe the user doesn't have access to the parent forum/text channel. If the server uses private threads in a text channel, the bot does not have the `manage_messages` permission in this channel.",
        "cannot_execute_text": "Cannot execute action on a text channel.",
        "cannot_execute_thread": "Cannot execute action in a thread channel.",
        "ticket_cannot_claim_closed": "A ticket cannot be claimed if it is closed.",
        "bot_cannot_claim": "A bot cannot claim a ticket.",
        "ticket_cannot_unclaim_closed": "A ticket cannot be unclaimed if it is closed.",
        "cannot_transfer_bot": "You cannot transfer ownership of a ticket to a bot.",
        "cannot_add_bot": "You cannot add a bot to a ticket. ({member})",
        "already_owner": "This member is already the owner of this ticket. ({member})",
        "is_admin": "This member is an administrator for the tickets system. They will always have access to the ticket anyway. ({member})",
        "already_access": "This member already has access to this ticket. ({member})",
        "cannot_remove_bot": "You cannot remove a bot to a ticket ({member}).",
        "cannot_remove_owner": "You cannot remove the owner of this ticket. ({member})",
        "is_admin_remove": "This member is an administrator for the tickets system. They will always have access to the ticket. ({member})",
        "not_authorized": "This member is not in the list of those authorised to access the ticket. ({member})",

        # Confirmations
        "confirm_close": "Do you really want to close the ticket {id}?",
        "confirm_lock": "Do you really want to lock the ticket {id}?",
        "confirm_delete": "Do you really want to delete all the messages of the ticket {id}?",
        "logs_note": "If a logs channel is defined, an html file containing all the messages of this ticket will be generated. (Attachments are not supported, as they are saved with their Discord link.)",

        # Button interactions
        "profile_button_not_exist": "The profile for which this button was configured no longer exists.",
        "profile_dropdown_not_exist": "The profile for which this dropdown was configured no longer exists.",
        "not_allowed_command": "You are not allowed to execute this command.",
        "chosen_create": "You have chosen to create a ticket.",
        "chosen_close": "You have chosen to close this ticket. If this is not done, you do not have the necessary permissions to execute this command.",
        "chosen_reopen": "You have chosen to re-open this ticket.",
        "chosen_claim": "You have chosen to claim this ticket. If this is not done, you do not have the necessary permissions to execute this command.",
        "chosen_create_reason": "You have chosen to create a ticket with the reason `{reason}`.",
        "not_in_config": "This message is not in TicketTool config.",

        # Other
        "no_tickets": "No tickets to show.",
        "no_open_tickets": "No open tickets by this user in this server.",
        "provide_info": "Please provide the required informations by clicking on the button below.",
        "not_allowed_interaction": "You are not allowed to use this interaction.",
        "cannot_create_bot": "You cannot create a ticket for a bot.",
        "cannot_create_higher": "You cannot create a ticket for a member with a higher or equal role.",
        "export_message": "Here is the html file of the transcript of all the messages in this ticket.\nPlease note: all attachments and user avatars are saved with the Discord link in this file.",
        "system_not_enabled_short": "The ticket system is not enabled on this server.",
        "author_message": "I have to be the author of the message. You can use EmbedUtils by AAA3A to send one.",
        "no_permissions_channel": "I don't have sufficient permissions in this channel to view it and to send messages into.",
        "dropdown_unique": "A different value must be provided for each dropdown option.",
        "invalid_emoji": "An emoji you selected seems invalid. Check that it is an emoji. If you have Nitro, you may have used a custom emoji from another server.",

        # Default embed description
        "default_embed_description": "To get help on this server or to make an order for example, you can create a ticket.\nJust use the command `{prefix}ticket create` or click on the button below.\nYou can then use the `{prefix}ticket` subcommands to manage your ticket.",

        # Language command
        "language_set": "Language has been set to **English**.",
        "language_current": "Current language: **{lang}**",
        "language_invalid": "Invalid language. Choose `en` for English or `pt-br` for Portuguese (Brazil).",
    },

    "pt-br": {
        # Button labels
        "create_ticket": "Criar bilhete",
        "close": "Fechar",
        "re_open": "Reabrir",
        "claim": "Reivindicação",
        "delete": "Eliminar",

        # Dropdown
        "choose_reason": "Escolha a razão para abrir um bilhete.",

        # Ticket actions
        "action_taken": "Medidas tomadas para o sistema de bilhetes.",
        "ticket_action_title": "Bilhete [{profile}] {id} - Acção tomada",

        # Embed fields
        "ticket_id": "Identificação do bilhete:",
        "owned_by": "Propriedade de:",
        "channel": "Canal:",
        "closed_by": "Fechado por:",
        "deleted_by": "Eliminado por:",
        "closed_at": "Fechado em:",
        "reason": "Motivo:",

        # Ticket status messages
        "ticket_created": "Bilhete Criado",
        "ticket_created_thanks": "Obrigado por criar um bilhete neste servidor!",
        "ticket_created_by": "O bilhete foi criado por {created_by}.",
        "report_creation": "Relatório sobre a criação do bilhete {id}.",

        "ticket_opened": "Bilhete aberto",
        "ticket_opened_by": "O bilhete foi aberto por {opened_by}.",
        "report_close": "Relatório sobre o encerramento do bilhete {id}.",

        "ticket_closed": "Bilhete Fechado",
        "ticket_closed_by": "O bilhete foi fechado por {closed_by}.",

        "ticket_locked": "Bilhete Bloqueado",
        "ticket_locked_by": "O bilhete foi bloqueado por {locked_by}.",
        "report_lock": "Relatório sobre a fechadura do bilhete {id}.",

        "ticket_unlocked": "Bilhete Desbloqueado",
        "ticket_unlocked_by": "O bilhete foi desbloqueado por {unlocked_by}.",
        "report_unlock": "Relatório sobre o desbloqueio do bilhete {id}.",

        "ticket_renamed": "Bilhete cujo nome foi alterado.",

        "ticket_deleted": "Bilhete Eliminado",
        "ticket_deleted_by": "O bilhete foi eliminado por {deleted_by}.",
        "report_deletion": "Relatório sobre a eliminação do bilhete {id}.",

        "ticket_claimed": "Pedido de bilhetes.",
        "ticket_unclaimed": "Bilhete não reclamado.",
        "owner_modified": "Proprietário Modificado.",

        # Audit reasons
        "creating_ticket": "Criação do bilhete {id}.",
        "opening_ticket": "Abertura do bilhete {id}.",
        "closing_ticket": "Fechando o bilhete {id}.",
        "locking_ticket": "Bloqueando o bilhete {id}.",
        "unlocking_ticket": "Desbloqueando o bilhete {id}.",
        "renaming_ticket": "Renomear o bilhete {id}. (`{old_name}` para `{new_name}`)",
        "deleting_ticket": "Eliminação do bilhete {id}.",
        "claiming_ticket": "Reivindicação do bilhete {id}.",
        "unclaiming_ticket": "Desclassificação do bilhete {id}.",
        "changing_owner": "Mudança de proprietário do bilhete {id}.",
        "adding_member": "Acrescentar um membro ao bilhete {id}.",
        "removing_member": "Retirar um membro para o bilhete {id}.",

        # Channel topic
        "channel_topic": "🎟️ ID do bilhete: {id}\n🕵️ Ticket criado por: @{created_by_name} ({created_by_id})\n☢️ Motivo do bilhete: {short_reason}\n",

        # Error messages
        "profile_not_exist": "Este perfil não existe.",
        "not_in_ticket": "Não está num bilhete.",
        "ticket_not_status": "Este bilhete não é {status}ed.",
        "ticket_is_status": "Este bilhete é {status}.",
        "not_allowed_lock": "Não é permitido bloquear este bilhete.",
        "not_allowed_view": "Não tem permissão para visualizar este bilhete.",
        "provide_profile": "Forneça um perfil.",
        "no_profile_created": "Nenhum perfil foi criado nesse servidor.",
        "system_not_enabled": "O sistema de bilhetes não está ativado neste servidor. Por favor, peça a um administrador deste servidor para usar os subcomandos `{prefix}settickettool` para configurá-lo.",
        "category_not_configured": "A categoria `open` ou a categoria `close` não foram configuradas. Por favor, peça a um administrador deste servidor que utilize os subcomandos `{prefix}settickettool` para as configurar.",
        "limit_reached": "Desculpe. Já atingiu o limite de {limit} bilhetes abertos.",
        "no_manage_channels": "O bot não tem permissão de \"gerir_canais\" nas categorias \"abrir\" e \"fechar\" para permitir que o sistema de bilhetes funcione correctamente. Por favor, notifique um administrador deste servidor.",
        "no_manage_forum": "O bot não tem `gestão_canal` permissão no canal do fórum para permitir que o sistema de bilhetes funcione correctamente. Por favor, notifique um administrador deste servidor.",
        "dynamic_name_error": "O nome do canal dinâmico não contém os nomes correctos das variáveis e deve ser reconfigurado com `[p]settickettool dynamicchannelname`.",
        "thread_add_error": "Pelo menos um usuário (o proprietário do tíquete ou um membro da equipe) não pôde ser adicionado ao tópico do tíquete. Talvez o usuário não tenha acesso ao fórum/canal de texto principal. Se o servidor usa tópicos privados em um canal de texto, o bot não tem a permissão `manage_messages` nesse canal.",
        "cannot_execute_text": "Não é possível executar acções num canal de texto.",
        "cannot_execute_thread": "Não é possível executar uma ação num canal de discussão.",
        "ticket_cannot_claim_closed": "Um bilhete não pode ser reclamado se estiver fechado.",
        "bot_cannot_claim": "Um robô não pode reclamar um bilhete.",
        "ticket_cannot_unclaim_closed": "Um bilhete não pode ser retirado se estiver fechado.",
        "cannot_transfer_bot": "Não se pode transferir a propriedade de um bilhete para um bot.",
        "cannot_add_bot": "Não se pode acrescentar um bot a um bilhete. ({member})",
        "already_owner": "Este membro já é o proprietário deste bilhete. ({member})",
        "is_admin": "Esse membro é um administrador do sistema de tíquetes. De qualquer forma, ele sempre terá acesso ao tíquete. ({member})",
        "already_access": "Este membro já tem acesso a este bilhete. ({member})",
        "cannot_remove_bot": "Não se pode remover um bot para um bilhete ({member}).",
        "cannot_remove_owner": "Não é possível remover o proprietário deste bilhete. ({member})",
        "is_admin_remove": "Esse membro é um administrador do sistema de tíquetes. Ele sempre terá acesso ao tíquete. ({member})",
        "not_authorized": "Este membro não consta da lista de pessoas autorizadas a aceder ao bilhete. ({member})",

        # Confirmations
        "confirm_close": "Quer mesmo fechar o bilhete {id}?",
        "confirm_lock": "Quer realmente trancar o bilhete {id}?",
        "confirm_delete": "Quer mesmo apagar todas as mensagens do bilhete {id}?",
        "logs_note": "Se for definido um canal de registros, será gerado um arquivo html com todas as mensagens desse tíquete. (Não há suporte para anexos, pois eles são salvos com seu link do Discord.)",

        # Button interactions
        "profile_button_not_exist": "O perfil para o qual este botão foi configurado já não existe.",
        "profile_dropdown_not_exist": "O perfil para o qual este menu suspenso foi configurado já não existe.",
        "not_allowed_command": "Não está autorizado a executar este comando.",
        "chosen_create": "Optou por criar um bilhete.",
        "chosen_close": "Optou por fechar este bilhete. Se isto não for feito, não tem as permissões necessárias para executar este comando.",
        "chosen_reopen": "Optou por reabrir este bilhete.",
        "chosen_claim": "Optou por reclamar este bilhete. Se isto não for feito, não tem as permissões necessárias para executar este comando.",
        "chosen_create_reason": "Optou por criar um bilhete com o motivo `{reason}`.",
        "not_in_config": "Esta mensagem não se encontra na configuração do TicketTool.",

        # Other
        "no_tickets": "Não há bilhetes para o espectáculo.",
        "no_open_tickets": "Não existem bilhetes abertos por este utilizador neste servidor.",
        "provide_info": "Forneça as informações necessárias clicando no botão abaixo.",
        "not_allowed_interaction": "Não tem permissão para usar esta interação.",
        "cannot_create_bot": "Não é possível criar um tíquete para um bot.",
        "cannot_create_higher": "Não é possível criar um tíquete para um membro com uma função superior ou igual.",
        "export_message": "Aqui está o ficheiro html da transcrição de todas as mensagens contidas neste bilhete.\nAtenção: todos os anexos e avatares de utilizadores são guardados com a ligação Discord neste ficheiro.",
        "system_not_enabled_short": "O sistema de bilhetes não está activado neste servidor.",
        "author_message": "Eu preciso ser o autor da mensagem. Você pode usar o EmbedUtils by AAA3A para enviar uma.",
        "no_permissions_channel": "Não tenho permissões suficientes neste canal para o visualizar e para enviar mensagens.",
        "dropdown_unique": "Deve ser fornecido um valor diferente para cada opção do menu pendente.",
        "invalid_emoji": "Um emoji que seleccionou parece inválido. Verifique se se trata de um emoji. Se tiver Nitro, poderá ter utilizado um emoji personalizado de outro servidor.",

        # Default embed description
        "default_embed_description": "Para obter ajuda neste servidor ou para fazer uma encomenda, por exemplo, pode criar um bilhete.\nBasta utilizar o comando `{prefix}ticket create` ou clicar no botão abaixo.\nPode então utilizar os subcomandos `{prefix}ticket` para gerir o seu bilhete.",

        # Language command
        "language_set": "O idioma foi definido para **Português (Brasil)**.",
        "language_current": "Idioma atual: **{lang}**",
        "language_invalid": "Idioma inválido. Escolha `en` para Inglês ou `pt-br` para Português (Brasil).",
    }
}

LANGUAGE_NAMES = {
    "en": "English",
    "pt-br": "Português (Brasil)"
}

def get_text(lang: str, key: str, **kwargs) -> str:
    """Get a localized string for the given language and key.

    Args:
        lang: Language code ('en' or 'pt-br')
        key: The string key to look up
        **kwargs: Format arguments for the string

    Returns:
        The localized string, or the English version if not found
    """
    lang = lang.lower() if lang else "en"
    if lang not in STRINGS:
        lang = "en"

    text = STRINGS[lang].get(key) or STRINGS["en"].get(key, key)

    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, ValueError):
            pass

    return text
