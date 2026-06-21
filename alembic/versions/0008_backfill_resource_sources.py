"""backfill resource sources for existing resources

Revision ID: 0008_backfill_resource_sources
Revises: 0007_resource_sources
Create Date: 2026-06-12 17:00:00
"""

from typing import Sequence, Union

from alembic import op


revision: str = "0008_backfill_resource_sources"
down_revision: Union[str, None] = "0007_resource_sources"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        insert into resource_sources (
            id,
            company_id,
            resource_id,
            source_type,
            source_account_id,
            source_account_label,
            access_level,
            can_sync,
            priority,
            visibility_scope,
            allowed_user_ids,
            allowed_roles,
            allowed_departments,
            limitations,
            settings,
            last_seen_at,
            created_at,
            updated_at
        )
        select
            gen_random_uuid(),
            r.company_id,
            r.id,
            case
                when r.platform = 'feishu' and r.app_config_id is not null then 'feishu_app'
                when r.platform in ('mail', 'email') or (r.resource_type like 'mail_%' and r.app_config_id is null) then 'mail_account'
                else 'manual_import'
            end as source_type,
            coalesce(r.app_config_id::text, '') as source_account_id,
            coalesce(f.name, f.app_id) as source_account_label,
            'read' as access_level,
            r.enabled as can_sync,
            case
                when r.platform = 'feishu' and r.app_config_id is not null then 100
                when r.platform in ('mail', 'email') or (r.resource_type like 'mail_%' and r.app_config_id is null) then 70
                else 20
            end as priority,
            coalesce(nullif(r.permission_level, ''), 'company') as visibility_scope,
            '[]'::jsonb as allowed_user_ids,
            '[]'::jsonb as allowed_roles,
            '[]'::jsonb as allowed_departments,
            '{}'::jsonb as limitations,
            jsonb_build_object('backfilled', true, 'from_resource_app_config_id', r.app_config_id::text) as settings,
            coalesce(r.updated_at, now()) as last_seen_at,
            now() as created_at,
            now() as updated_at
        from resources r
        left join feishu_app_configs f on f.id = r.app_config_id
        where not exists (
            select 1
            from resource_sources s
            where s.resource_id = r.id
              and s.source_type = case
                    when r.platform = 'feishu' and r.app_config_id is not null then 'feishu_app'
                    when r.platform in ('mail', 'email') or (r.resource_type like 'mail_%' and r.app_config_id is null) then 'mail_account'
                    else 'manual_import'
                  end
              and s.source_account_id = coalesce(r.app_config_id::text, '')
        )
        """
    )
    op.execute(
        """
        update work_events e
        set
            source_type = case
                when r.platform = 'feishu' and r.app_config_id is not null then 'feishu_app'
                when r.platform in ('mail', 'email') or (r.resource_type like 'mail_%' and r.app_config_id is null) then 'mail_account'
                else e.source_type
            end,
            source_account_id = coalesce(e.source_account_id, r.app_config_id::text),
            visibility_scope = coalesce(nullif(r.permission_level, ''), e.visibility_scope, 'company'),
            updated_at = now()
        from resources r
        where e.resource_id = r.id
          and (e.source_type = 'unknown' or e.source_account_id is null or e.visibility_scope = 'company')
        """
    )


def downgrade() -> None:
    op.execute("delete from resource_sources where settings ->> 'backfilled' = 'true'")
