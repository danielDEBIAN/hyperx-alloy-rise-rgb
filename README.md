# HyperX Alloy Rise 75 RGB (Linux)

Controlador RGB **nativo para Linux** para el teclado **HyperX Alloy Rise 75** (`03f0:02a1` / `03f0:03a1`), sin necesidad de Windows, máquinas virtuales ni HyperX NGENUITY.

Incluye:
- **Interfaz gráfica nativa en GTK 4** (`hyperx-rise-gui`) con **mapa interactivo del teclado** (control **por tecla individual**, arrastrar para pintar, cuentagotas, selección múltiple y **por zonas** incluyendo barras laterales), además de efectos animados, temas rápidos de 1 clic, brillo y velocidad en vivo.
- **Herramienta CLI** (`hyperx-rise-rgb`) para automatización y scripts (colores, efectos, zonas y teclas individuales).
- **Servicio ligero en segundo plano** (`systemd --user`, ~9 MB RAM, < 0.1% CPU) que mantiene tus colores y animaciones activos de forma fluida y se reconecta automáticamente al suspender o reconectar el teclado.
- **Soporte dual `hidraw` + `libusb-1.0`**: evita automáticamente el *quirk* de `usbhid` en Linux (`error -71` en reconexión en caliente en la interfaz 2).

## Efectos soportados (102 LEDs + barras laterales)

- **Sólido** (`solid`)
- **Dos zonas** (`twozone`): color base para teclas + acentos en `ESC`, `WASD`, flechas y barras laterales
- **Respiración** (`breath`)
- **Ola direccional** (`wave`: izquierda, derecha, arriba, abajo, centro afuera/adentro)
- **Ola espejo** (`mirrorwave`)
- **Ciclo de color** (`cycle`)
- **Confeti / Destellos** (`confetti`)
- **Alternado** (`alternating`)
- **Apagado** (`off`)
- **Brillo** (`0–100%`) y botón para pausar el control en Linux y volver al efecto interno del teclado.

## Requisitos

- Linux con **Python 3.10+**
- `libusb-1.0` (incluida en la mayoría de distros)
- Para la interfaz gráfica: **GTK 4** y **PyGObject** (`python3-gobject` / `gtk4`, preinstalados en Fedora/Bazzite, Ubuntu/GNOME, etc.)
- Sin dependencias `pip` externas.

## Instalación

```bash
git clone <URL_DE_TU_REPO>
cd hyperx-alloy-rise-rgb
./install.sh
```

El instalador copia la app a `~/.local/share/hyperx-alloy-rise-rgb`, crea el acceso directo **HyperX Alloy Rise 75 RGB** en tu menú de aplicaciones, instala la regla `udev` (`60-hyperx-alloy-rise.rules`) para usar el teclado sin `sudo`, y habilita el servicio `hyperx-alloy-rise-rgb.service`.

Para desinstalar:

```bash
./uninstall.sh
```

## Uso rápido

### Interfaz gráfica
Abre **HyperX Alloy Rise 75 RGB** desde tu menú de aplicaciones o ejecuta:

```bash
hyperx-rise-gui
```

### Línea de comandos (CLI)

```bash
# Color sólido
hyperx-rise-rgb solid 9B30FF

# Pintar una zona específica (alphas, numbers, fkeys, wasd, arrows, nav, modifiers, sidebars...)
hyperx-rise-rgb zone wasd 39FF14
hyperx-rise-rgb zone sidebars 00E5FF

# Pintar una tecla individual
hyperx-rise-rgb key esc FF1E27
hyperx-rise-rgb key space 9B30FF

# Tema de dos zonas (teclas moradas + WASD/ESC/flechas/barras verdes)
hyperx-rise-rgb effect twozone --color 9B30FF --color2 39FF14 --color-type two

# Ola arcoíris hacia la derecha
hyperx-rise-rgb effect wave --color-type spectrum --direction right --delay 45

# Respiración alternando 2 colores
hyperx-rise-rgb effect breath --color 9B30FF --color2 39FF14 --color-type two

# Ajustar brillo (0-100)
hyperx-rise-rgb brightness 80

# Pausar el control en Linux y volver al efecto interno del teclado
hyperx-rise-rgb native
```

La configuración actual se guarda en `~/.config/hyperx-alloy-rise-rgb/config.json`.

## Detalles del protocolo HID

El **HyperX Alloy Rise 75** expone la interfaz vendor **Interface 2** (`Usage Page 0xFF90`, `Usage 0xFF00`, endpoints Interrupt `EP OUT 0x04` / `EP IN 0x83`, paquetes de 64 bytes):

- **Brillo:** `[0x40, 0x01, 0x00, 0x00, raw_0_255, ...]`
- **Modo Directo (102 LEDs, índices 1..103):**
  - Encabezado: `[0x44, 0x01, 0x06, ...]` (6 paquetes × 20 LEDs por cuadro; `0x00` para salir)
  - Datos (`pkt = 0..5`): `[0x44, 0x02, pkt, 0x00, R0, G0, B0, ..., R19, G19, B19]`
  - Cada paquete genera confirmación USB IN (`0x45 0x02` + `0xFF 0x01 ... 0x44 0x02`), por lo que el daemon sincroniza y vacía las respuestas en *lockstep* (~7 ms por cuadro completo).

## Licencia

[MIT](LICENSE)
