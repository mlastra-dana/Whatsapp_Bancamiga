# WABA Center - Nota historica

Este archivo queda como referencia para quienes encuentren menciones antiguas a `ChattLogger`.

El producto activo de este repositorio es **WABA Center**. La documentacion principal y vigente esta en:

```text
../README.md
```

## Estado actual

- Backend activo: `manual-chat-lambda/handler.py`
- Lambda productivo en AWS Console: `VZla-Chatt_logger`
- Frontend activo: `panel/waba-center.html` y `panel/conversaciones.html`
- Hosting: Amplify o hosting estatico equivalente

## Nota

El nombre `ChattLogger` se conserva en algunas variables historicas del frontend, por ejemplo `CHATTLOGGER_API_URL`, para no romper configuraciones existentes. A nivel funcional y de producto, el alcance actual debe entenderse como **WABA Center**.

Los modulos de Bedrock, calendario, citas y lambdas antiguas no forman parte del flujo activo salvo que se reactive explicitamente ese alcance.

El inventario tecnico de esos componentes heredados esta en:

```text
legacy-components.md
```
