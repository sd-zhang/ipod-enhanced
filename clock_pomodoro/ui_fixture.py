"""Native widget/VM integration with supplied display and OS delivery boundaries."""
from unittest.mock import patch

from verify_runtime import RuntimeClock
from native_fixture import ScriptVM, APP, RENDERER
from verify_resources import directory, records, word
from verify_actions_widgets import ActionsDisplayFixture
import verify_actions
from unicorn import UC_HOOK_CODE, arm_const as arm


class UIClock(RuntimeClock, ScriptVM):
    def dispatch(self, cpu, pc, size, context):
        # Only the Pomodoro tree is instantiated. Clock/Home restoration is
        # the display boundary; their history and leave callbacks still run.
        if pc in (0xdd19c, 0x7b1b4) and self.reg(1) != 0x6140:
            # Native show (85294) replaces the current view at +20 and clears
            # its focused menu at +24. Represent the omitted view as null;
            # retaining Pomodoro here triggers its same-view early return on
            # reopen, skipping native focus discovery and our timer setup.
            self.write(RENDERER + 0x20, 0, 0)
            cpu.reg_write(arm.UC_ARM_REG_R0, 0)
            cpu.reg_write(arm.UC_ARM_REG_PC, cpu.reg_read(arm.UC_ARM_REG_LR))
            return
        super().dispatch(cpu, pc, size, context)


def clock_menu_tree(m, image):
    view = directory(image)['View'][1][0x250d]
    record = next(r for r in records(view) if word(r, 12) == 0x4582)
    m.cpu.mem_write(0x1001a000, record[4:])
    root = m.call(0x4c454, 0, 0, 0x1001a000, 0x707a8)
    first, separator, header = ActionsDisplayFixture.children(m, root)
    menu = (first if m.read(first + 0x1c) & 0xffff == 0x4581
            else ActionsDisplayFixture.children(m, first)[0])
    # Supply only the physical content area. Stock layout resolves all child
    # bounds, including row height, viewport clipping, and the Clock header.
    m.write(root + 0x58, 0, 0, 108, 160)
    m.write(root + 0x78, 0, 0, 108, 160)
    del m.hooks[0x3be44]
    m.call(0x3bf98, root, 0)
    m.hook(0x65eec, lambda: m.reg(0))  # enclosing display observer registration
    m.call(0x660f0, root)
    m.call(0x3bf98, root, 0)
    return menu, separator, header


def renderer_event(m, event):
    table = m.read(RENDERER)
    return m.call(table + m.read(table + 0x24), RENDERER, event)


def machine(image, symbols):
    with patch.object(verify_actions, 'ScriptVM', lambda data: UIClock(data, symbols)):
        m = verify_actions.menu_machine(image)
    # Enter from Clock with ordinary native history and commands. Its rendering
    # is supplied when Menu returns, but history pop/leave/restoration execute.
    m.write(m.read(APP + 0x104) + 8, 2442 * 4 + 2)
    m.write(m.read(APP + 0x104) + 0x10, 0)
    m.write(m.read(APP + 0x104) + 0x14, 0)
    m.supply_services(((2, (0x6f,)),))
    m.hook(0x9f9a8, lambda: 0x10018000)
    m.hook(0x9f9bc, lambda: 0x10018800)  # current UI task identity
    m.hook(0xd9a60, m.allocate)
    m.hook(0xd97b8, m.allocate)
    m.hook(0xd9a50, m.free)
    m.hook(0xdda9c, lambda: ActionsDisplayFixture.reallocate(m))
    m.hook(0x6c760, lambda: 0)
    registry = {}
    m.hook(0xbac78, lambda: registry.update({m.reg(1): m.reg(0)}) or 0)
    m.hook(0x64a48, lambda: registry.get(m.reg(0), 0))
    m.hook(0x3be44, lambda: 0)  # enclosing display geometry/font service
    resources = directory(image)
    m.resource_addresses = {(int.from_bytes(tag.encode(), 'big'), key): image.find(data)
                            for tag, (_, entries) in resources.items() for key, data in entries.items()}
    m.hook(0x72a88, lambda: m.resource_addresses.get((m.reg(0), m.reg(1)), 0))
    # The actual Renderer inherits an enclosing resource provider. Supply
    # that directory lookup at the boundary so an unhandled dynamic property
    # really falls back to its static resource, as it does on the iPod.
    resource_parent, resource_table, resource_getter = 0x10019000, 0x10019100, 0x10019200
    m.write(resource_parent, resource_table)
    m.write(resource_table + 0x50, resource_getter - resource_table)
    def fallback_resource():
        at = m.resource_addresses.get((m.reg(1), m.reg(2)), 0)
        m.write(m.reg(3), at)
        return int(at != 0)
    m.hook(resource_getter, fallback_resource)
    # Hardware constructs the iPod Renderer subclass, not its base class.
    # Execute that constructor, including its real vtable selection and
    # registration; only the font-style service is supplied here.
    m.hook(0xcf128, lambda: 0)
    m.call(0xf2c04, RENDERER, resource_parent)
    controller_table = m.read(RENDERER)
    m.call(0xba5f0)
    row = next(r for r in records(resources['View'][1][0x250d]) if word(r, 12) == 0x6140)
    m.cpu.mem_write(0x1001a000, row[4:])
    m.display_root = m.call(0x4c454, 0, 0, 0x1001a000, 0x707a8)
    m.phase, m.countdown, m.menu = ActionsDisplayFixture.children(m, m.display_root)
    m.call(0x660f0, m.display_root)
    m.row = m.read(m.menu + 0x90)
    for obj, top, left, bottom, right in (
            (m.display_root, 0, 0, 108, 160), (m.phase, 4, 4, 22, 156),
            (m.countdown, 26, 35, 56, 125), (m.menu, 60, 0, 108, 160),
            (m.row, 0, 0, 18, 160)):
        m.write(obj + 0x58, top, left, bottom, right)
    m.RENDERER, m.PARENT = RENDERER, 0x1001b000
    ActionsDisplayFixture.prepare_renderer(m)
    # The older generic display helper installs a base-class table. Keep the
    # table selected by the native constructor for every property/event call.
    m.write(RENDERER, controller_table)
    # Use the actual Renderer show function through the VM's native stub;
    # animation rasterization itself remains a display boundary.
    del m.hooks[m.method_address(3, 0x99)]
    del m.hooks[m.method_address(3, 0x91)]
    m.invalidations = []
    m.hook(0x9100c, lambda: m.invalidations.append(
        tuple(m.read(m.reg(1) + i * 4) for i in range(4))) or 0)
    m.hook(0x3f33c, lambda: 0)  # enclosing display observer
    m.properties = []
    # Observe notification requests without replacing their native delivery.
    # Registration, dispatch, widget handlers and rectangle invalidation run;
    # actual LCD rasterization remains a boundary.
    def property_request(cpu, pc, size, context):
        if m.reg(0) == RENDERER:
            m.properties.append((m.reg(1), m.reg(2)))
    m.cpu.hook_add(UC_HOOK_CODE, property_request, begin=0x39b2c, end=0x39b2c)
    # Timer bind/configuration/list insertion/removal execute natively.
    # Actual passage of time and timer-event delivery are supplied by tests.
    return m
