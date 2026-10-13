use bcs_domain::{GroupContextEntry, GroupContextPermission};

pub fn actor_can_read(entry: &GroupContextEntry, actor_id: &str) -> bool {
    entry.visible_to.is_visible_to(actor_id)
}

pub fn actor_can_write(entry: &GroupContextEntry, actor_id: &str) -> bool {
    entry.collect_from.contains_user(actor_id)
}

pub fn permission_for(entry: &GroupContextEntry, actor_id: &str) -> Option<GroupContextPermission> {
    GroupContextPermission::from_access(
        actor_can_read(entry, actor_id),
        actor_can_write(entry, actor_id),
    )
}
