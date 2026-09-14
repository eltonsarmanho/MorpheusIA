from app.llm.tools import (
    CATEGORY_LABELS,
    SAVE_LEAD_INFO_TOOL,
    build_system_prompt,
)


def test_save_lead_info_schema_requires_category_and_need_summary():
    required = SAVE_LEAD_INFO_TOOL["parameters"]["required"]

    assert set(required) == {"category", "need_summary"}


def test_save_lead_info_schema_category_enum_matches_solution_categories():
    category_schema = SAVE_LEAD_INFO_TOOL["parameters"]["properties"]["category"]

    assert category_schema["enum"] == [
        "gestao_empresas",
        "whatsapp_atendimento",
        "analise_documentos",
        "gerador_conteudo",
        "outro",
    ]
    # enum values are exactly CATEGORY_LABELS' keys (single source of truth)
    assert category_schema["enum"] == list(CATEGORY_LABELS.keys())


def test_save_lead_info_schema_contact_fields_are_optional():
    properties = SAVE_LEAD_INFO_TOOL["parameters"]["properties"]
    required = SAVE_LEAD_INFO_TOOL["parameters"]["required"]

    for optional_field in ("contact_name", "contact_phone", "contact_email"):
        assert optional_field in properties
        assert optional_field not in required


def test_build_system_prompt_asks_for_contact_when_not_already_asked():
    prompt = build_system_prompt(contact_already_asked=False)

    assert "pergunte UMA única vez" in prompt
    assert "telefone ou e-mail" in prompt


def test_build_system_prompt_omits_contact_ask_when_already_asked():
    prompt = build_system_prompt(contact_already_asked=True)

    assert "pergunte UMA única vez" not in prompt
    assert "telefone ou e-mail" not in prompt
