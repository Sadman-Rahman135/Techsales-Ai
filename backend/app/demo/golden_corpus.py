"""The golden-v1 corpus (blueprint §12.1): 21 fictional TechCare documents behind golden-v1
(`evaluation/golden_questions/`).

It extends the M3 corpus (app/demo/corpus.py) with documents chosen to exercise what §12 asks for:
- near-identical products: DemoPrinter-X2 vs DemoPrinter-X2 Pro, TechBook 14 vs TechBook 14 Pro (same error
  code E-04 with different meanings, different battery life);
- an outdated version: the Price Match Policy is published as v1 (14 days), then replaced by v2 (30 days);
- exact codes: printer E-/P- codes and hub H- codes;
- four employee-only documents, each with its own canary string, for the access probes (G5);
- PDF, Word and text files, single- and multi-page.

Every new document states that it is fictional. All names, prices and policies are invented for the demo.
"""

from app.db.models import Audience
from app.demo.builders import docx_bytes, pdf_bytes
from app.demo.corpus import DOCUMENTS as M3_DOCUMENTS
from app.demo.corpus import EMPLOYEE_CANARY, CorpusDocument

FICTIONAL = "This is a fictional document for the TechCare demo."
CANARIES = {
    "Repair Desk Procedures": EMPLOYEE_CANARY,
    "Employee Discount Policy": "CANARY-EMP-D15",
    "Store Opening and Closing Checklist": "CANARY-EMP-K44",
    "Refund Approval Rules": "CANARY-EMP-R90",
}


def _pdf(title: str, pages: list[list[tuple[str, str]]]) -> bytes:
    pages[-1] = [*pages[-1], ("p", FICTIONAL)]
    return pdf_bytes(pages, header=title, footer_page_numbers=True)


def _text(sections: list[str]) -> bytes:
    """Plain text; form feeds separate pages."""
    return ("\f".join(sections) + f"\n{FICTIONAL}\n").encode()


def printer_x2_pro() -> bytes:
    return _pdf(
        "DemoPrinter-X2 Pro Troubleshooting Guide",
        [
            [
                ("h1", "DemoPrinter-X2 Pro Troubleshooting Guide"),
                ("h2", "Faded prints"),
                (
                    "p",
                    "On the DemoPrinter-X2 Pro, faded prints usually mean Toner Saver mode is on. "
                    "Turn off Toner Saver in Settings > Eco.",
                ),
            ],
            [
                ("h2", "Error codes"),
                (
                    "table",
                    "Code|Meaning|What to do\n"
                    "E-04|Duplex unit not detected|Reseat the duplex unit at the back\n"
                    "E-09|Scanner lid open|Close the scanner lid\n"
                    "P-11|Paper size mismatch|Match the tray size in Settings",
                ),
            ],
            [
                ("h2", "Wi-Fi setup"),
                (
                    "p",
                    "The DemoPrinter-X2 Pro supports both 2.4 GHz and 5 GHz Wi-Fi networks. "
                    "Hold the Wi-Fi button for 5 seconds to start setup.",
                ),
            ],
        ],
    )


def techbook_14() -> bytes:
    return _pdf(
        "TechBook 14 User Guide",
        [
            [
                ("h1", "TechBook 14 User Guide"),
                ("h2", "Battery and charging"),
                (
                    "p",
                    "The TechBook 14 battery lasts up to 12 hours of web browsing. "
                    "A full charge takes about 2 hours with the 65 W charger.",
                ),
            ],
            [
                ("h2", "Keyboard and setup menu"),
                ("p", "Press Fn and F5 to turn the TechBook 14 keyboard backlight on or off."),
                ("p", "Press F2 during startup to open the TechBook 14 setup menu."),
            ],
            [
                ("h2", "Ports"),
                ("p", "The TechBook 14 has two USB-C ports, one USB-A port and a headphone jack."),
            ],
        ],
    )


def techbook_14_pro() -> bytes:
    return _pdf(
        "TechBook 14 Pro User Guide",
        [
            [
                ("h1", "TechBook 14 Pro User Guide"),
                ("h2", "Battery and charging"),
                (
                    "p",
                    "The TechBook 14 Pro battery lasts up to 18 hours of web browsing. "
                    "A full charge takes about 90 minutes with the 100 W charger.",
                ),
            ],
            [
                ("h2", "Keyboard and setup menu"),
                ("p", "Press Fn and F7 to turn the TechBook 14 Pro keyboard backlight on or off."),
                ("p", "Press F10 during startup to open the TechBook 14 Pro setup menu."),
            ],
            [
                ("h2", "Ports"),
                ("p", "The TechBook 14 Pro has three USB-C ports, an HDMI port and an SD card reader."),
            ],
        ],
    )


def care_plus() -> bytes:
    return _pdf(
        "Care+ Protection Plan Terms",
        [
            [
                ("h1", "Care+ Protection Plan Terms"),
                ("h2", "What Care+ covers"),
                (
                    "p",
                    "Care+ covers accidental damage, including drops and liquid spills, "
                    "for 24 months from purchase.",
                ),
                ("p", "Care+ must be bought within 30 days of buying the device."),
            ],
            [
                ("h2", "Claims"),
                ("p", "Each accidental damage claim has a 79 dollar service fee."),
                ("p", "Care+ allows two accidental damage claims per year."),
                ("p", "Theft and loss are not covered by Care+."),
            ],
        ],
    )


def price_match(version: int) -> bytes:
    window = "14 days" if version == 1 else "30 days"
    return _text(
        [
            f"# Price Match Policy (version {version})\n"
            f"TechCare matches the price of a local competitor within {window} of purchase.\n"
            "Online marketplaces and auction sites are excluded from price matching.\n"
            "A price match needs proof of the lower price, such as an advert or a web page.\n"
        ]
    )


def gift_cards() -> bytes:
    return _text(
        [
            "# Gift Cards FAQ\n"
            "TechCare gift cards never expire.\n"
            "Gift cards cannot be exchanged for cash.\n"
            "You can check a gift card balance at any store or on techcare.example/giftcards.\n"
            "A lost gift card can be replaced only with the original receipt.\n"
        ]
    )


def trade_in() -> bytes:
    return docx_bytes(
        [
            ("title", "Trade-In Program"),
            ("p", "Trade in an old phone, tablet or laptop for TechCare store credit."),
            ("p", "A trade-in device must power on and must not be reported stolen."),
            ("p", "Trade-in credit is paid as a TechCare gift card."),
            (
                "table",
                "Device|Maximum trade-in credit\nPhone|300 dollars\nTablet|200 dollars\nLaptop|400 dollars",
            ),
            ("p", FICTIONAL),
        ]
    )


def shipping() -> bytes:
    return docx_bytes(
        [
            ("title", "Shipping and Delivery"),
            ("h1", "Delivery times"),
            ("p", "Standard delivery takes 3 to 5 business days."),
            ("p", "Express delivery arrives the next business day if you order before 2pm."),
            ("p", "Delivery is free on orders over 50 dollars."),
            (
                "p",
                "Televisions and other large items are delivered by a two-person team who will unpack them.",
            ),
            ("pagebreak", ""),
            ("h1", "Store pickup"),
            ("p", "Orders for store pickup are ready within 2 hours."),
            ("p", "Bring photo ID and the order confirmation email when you collect an order."),
            ("p", FICTIONAL),
        ]
    )


def order_changes() -> bytes:
    return _text(
        [
            "# Order Changes and Cancellations\n"
            "You can cancel an order free of charge until it ships.\n"
            "After an order ships, return it under the Returns Policy instead of cancelling it.\n"
            "To change a delivery address, contact support before the order ships.\n"
        ]
    )


def data_transfer() -> bytes:
    return _pdf(
        "Data Transfer Service",
        [
            [
                ("h1", "Data Transfer Service"),
                (
                    "p",
                    "The data transfer service moves photos, contacts and files "
                    "from an old device to a new one.",
                ),
                ("p", "Data transfer costs 39 dollars and is free with any new laptop purchase."),
                ("p", "Bring both devices and their passwords; most data transfers take about 1 hour."),
                ("p", "TechCare deletes any temporary copy of your data within 24 hours."),
            ]
        ],
    )


def screen_repair() -> bytes:
    return _pdf(
        "Phone Screen Repair Service",
        [
            [
                ("h1", "Phone Screen Repair Service"),
                ("p", "Screen repairs for most phones are completed the same day."),
                (
                    "p",
                    "A screen repair costs 129 dollars for standard phones and 199 dollars for large phones.",
                ),
                ("p", "Every screen repair comes with a 90 day repair warranty."),
                (
                    "p",
                    "Back up your phone before a screen repair, "
                    "because the phone may be reset during testing.",
                ),
            ]
        ],
    )


def smart_hub() -> bytes:
    return _pdf(
        "Smart Hub H1 Setup Guide",
        [
            [
                ("h1", "Smart Hub H1 Setup Guide"),
                ("h2", "Setup"),
                ("p", "Plug in the Smart Hub H1 and wait for the ring light to turn white."),
                ("p", "Open the TechCare Home app and tap Add device to pair the Smart Hub H1."),
            ],
            [
                ("h2", "Hub error codes"),
                (
                    "table",
                    "Code|Meaning|What to do\nH-01|No internet connection|Check your router\n"
                    "H-03|Pairing timed out|Hold the reset button for 10 seconds and pair again\n"
                    "H-07|Firmware update failed|Restart the hub and leave it on for 15 minutes",
                ),
            ],
        ],
    )


def earbuds() -> bytes:
    return _text(
        [
            "# Wireless Earbuds E3 FAQ\n"
            "To pair the E3 earbuds, open the case and hold the button on the back for 3 seconds until the "
            "light flashes white.\n"
            "The E3 earbuds play music for 6 hours, and the charging case adds 18 more hours.\n"
            "The E3 earbuds are rated IPX4, so they resist sweat and splashes but are not waterproof.\n"
            "To reset the E3 earbuds, hold the case button for 15 seconds.\n"
        ]
    )


def employee_discount() -> bytes:
    canary = CANARIES["Employee Discount Policy"]
    return docx_bytes(
        [
            ("title", "Employee Discount Policy"),
            ("p", f"Internal reference {canary}. Staff use only; never share with customers."),
            ("p", "Employees get a 15% staff discount on most products after 90 days of employment."),
            ("p", "The staff discount does not apply to gift cards or Care+."),
            ("p", "Employees may buy at most 3 laptops per year with the staff discount."),
            ("p", FICTIONAL),
        ]
    )


def opening_checklist() -> bytes:
    canary = CANARIES["Store Opening and Closing Checklist"]
    return _text(
        [
            "# Store Opening and Closing Checklist\n"
            f"Internal reference {canary}. Staff use only.\n"
            "Disarm the alarm with the store code and turn on the demo devices by 8:45am.\n"
            "Count the cash drawer with a second employee before opening.\n"
            "At closing, lock laptops, phones and other high-value items in the back room.\n"
        ]
    )


def refund_rules() -> bytes:
    canary = CANARIES["Refund Approval Rules"]
    return _pdf(
        "Refund Approval Rules",
        [
            [
                ("h1", "Refund Approval Rules"),
                ("p", f"Internal reference {canary}. Staff use only."),
                ("p", "Refunds over 500 dollars need a manager approval code in the till."),
                ("p", "Refunds without a receipt are given as store credit only."),
                ("p", "Tell the store manager about any customer with more than 3 returns in 30 days."),
            ]
        ],
    )


NEW_DOCUMENTS: tuple[CorpusDocument, ...] = (
    CorpusDocument(
        "DemoPrinter-X2-Pro-Troubleshooting.pdf",
        "DemoPrinter-X2 Pro Troubleshooting Guide",
        Audience.PUBLIC,
        printer_x2_pro,
        product_model="DemoPrinter-X2 Pro",
        category="Troubleshooting",
    ),
    CorpusDocument(
        "TechBook-14-User-Guide.pdf",
        "TechBook 14 User Guide",
        Audience.PUBLIC,
        techbook_14,
        product_model="TechBook 14",
        category="Manuals",
    ),
    CorpusDocument(
        "TechBook-14-Pro-User-Guide.pdf",
        "TechBook 14 Pro User Guide",
        Audience.PUBLIC,
        techbook_14_pro,
        product_model="TechBook 14 Pro",
        category="Manuals",
    ),
    CorpusDocument(
        "Care-Plus-Terms.pdf", "Care+ Protection Plan Terms", Audience.PUBLIC, care_plus, category="Warranty"
    ),
    CorpusDocument(
        "Price-Match-Policy.txt",
        "Price Match Policy",
        Audience.PUBLIC,
        lambda: price_match(1),
        category="Policies",
        revisions=(lambda: price_match(2),),
    ),
    CorpusDocument("Gift-Cards-FAQ.txt", "Gift Cards FAQ", Audience.PUBLIC, gift_cards, category="Policies"),
    CorpusDocument(
        "Trade-In-Program.docx", "Trade-In Program", Audience.PUBLIC, trade_in, category="Services"
    ),
    CorpusDocument(
        "Shipping-and-Delivery.docx", "Shipping and Delivery", Audience.PUBLIC, shipping, category="Orders"
    ),
    CorpusDocument(
        "Order-Changes.txt",
        "Order Changes and Cancellations",
        Audience.PUBLIC,
        order_changes,
        category="Orders",
    ),
    CorpusDocument(
        "Data-Transfer-Service.pdf",
        "Data Transfer Service",
        Audience.PUBLIC,
        data_transfer,
        category="Services",
    ),
    CorpusDocument(
        "Phone-Screen-Repair.pdf",
        "Phone Screen Repair Service",
        Audience.PUBLIC,
        screen_repair,
        category="Services",
    ),
    CorpusDocument(
        "Smart-Hub-H1-Setup.pdf",
        "Smart Hub H1 Setup Guide",
        Audience.PUBLIC,
        smart_hub,
        product_model="Smart Hub H1",
        category="Manuals",
    ),
    CorpusDocument(
        "Earbuds-E3-FAQ.txt",
        "Wireless Earbuds E3 FAQ",
        Audience.PUBLIC,
        earbuds,
        product_model="Earbuds E3",
        category="Manuals",
    ),
    CorpusDocument(
        "Employee-Discount-Policy.docx",
        "Employee Discount Policy",
        Audience.EMPLOYEE,
        employee_discount,
        category="Internal",
    ),
    CorpusDocument(
        "Store-Checklist.txt",
        "Store Opening and Closing Checklist",
        Audience.EMPLOYEE,
        opening_checklist,
        category="Internal",
    ),
    CorpusDocument(
        "Refund-Approval-Rules.pdf",
        "Refund Approval Rules",
        Audience.EMPLOYEE,
        refund_rules,
        category="Internal",
    ),
)

DOCUMENTS: tuple[CorpusDocument, ...] = (*M3_DOCUMENTS, *NEW_DOCUMENTS)
