from handlers import admin, admin_visual_creatives


def test_visual_creative_router_is_registered_under_admin_router():
    assert admin_visual_creatives.router in admin.router.sub_routers
