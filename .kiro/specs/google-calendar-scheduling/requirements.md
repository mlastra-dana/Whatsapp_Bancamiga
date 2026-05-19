# Documento de Requisitos — Google Calendar Scheduling (Action Group)

## Introducción

Este feature agrega un Action Group al Bedrock Agent existente del sistema WABA Bedrock Webhook, permitiendo que el agente consulte disponibilidad en múltiples calendarios de Google Workspace de miembros del equipo y cree reuniones de 30 minutos en Google Calendar. La integración se realiza mediante una cuenta de servicio de Google Cloud con delegación a nivel de dominio (domain-wide delegation) de Google Workspace. El objetivo es capturar oportunidades de negocio permitiendo a los usuarios de WhatsApp agendar reuniones de seguimiento después de un evento. Una nueva función Lambda maneja las llamadas a la Google Calendar API, y las credenciales de la cuenta de servicio se almacenan de forma segura en AWS Secrets Manager.

## Glosario

- **Bedrock_Agent**: Agente de Amazon Bedrock existente (ID: RFEC7ZOIPI) que orquesta las consultas a la Knowledge Base y ejecuta Action Groups.
- **Calendar_Action_Group**: Action Group de Amazon Bedrock que expone las acciones `check_availability` y `create_event` al Bedrock_Agent.
- **Calendar_Lambda**: Función AWS Lambda en Python que implementa la lógica del Calendar_Action_Group, realizando llamadas a la Google Calendar API.
- **Google_Calendar_API**: API REST de Google (v3) para consultar y crear eventos en calendarios de Google Workspace.
- **Service_Account**: Cuenta de servicio de Google Cloud configurada con delegación a nivel de dominio de Google Workspace para acceder a los calendarios del equipo.
- **Credentials_Secret**: Secreto en AWS Secrets Manager que almacena el JSON de credenciales de la Service_Account.
- **Team_Calendars**: Lista configurable de direcciones de correo electrónico de los miembros del equipo cuyos calendarios se consultan para disponibilidad.
- **Slot_Disponible**: Bloque de 30 minutos dentro del horario laboral (lunes a viernes, 9:00–17:00) en el que todos los miembros del equipo consultados están libres.
- **Horario_Laboral**: Ventana de tiempo de lunes a viernes, de 9:00 a 17:00 hora local configurada, durante la cual se permiten reuniones.
- **CDK_Stack**: Stack de AWS CDK existente (`WabaBedrockStack`) que define y despliega toda la infraestructura del sistema.
- **Webhook_Handler**: Función Lambda existente que recibe mensajes de WhatsApp y los procesa a través del Bedrock_Agent.
- **Timezone_Configurada**: Zona horaria configurable mediante variable de entorno que determina el horario laboral y la presentación de horarios al usuario.

## Requisitos

### Requisito 1: Acción de Consulta de Disponibilidad (`check_availability`)

**Historia de Usuario:** Como usuario de WhatsApp, quiero consultar la disponibilidad del equipo para una reunión, para que pueda elegir un horario conveniente para todos.

#### Criterios de Aceptación

1. WHEN el Bedrock_Agent invoca la acción `check_availability` con una fecha objetivo, THE Calendar_Lambda SHALL consultar la Google_Calendar_API para obtener los eventos existentes de cada calendario en Team_Calendars para esa fecha.
2. WHEN la Google_Calendar_API retorna los eventos existentes, THE Calendar_Lambda SHALL calcular los Slots_Disponibles de 30 minutos dentro del Horario_Laboral en los que todos los miembros consultados estén libres.
3. THE Calendar_Lambda SHALL retornar al Bedrock_Agent la lista de Slots_Disponibles con la hora de inicio y hora de fin de cada slot en formato legible para el usuario.
4. WHEN no existen Slots_Disponibles para la fecha solicitada, THE Calendar_Lambda SHALL retornar un mensaje indicando que no hay disponibilidad para esa fecha y sugerir consultar otra fecha.
5. THE Calendar_Lambda SHALL filtrar los slots para incluir únicamente los que caen dentro del Horario_Laboral (lunes a viernes, 9:00–17:00 en la Timezone_Configurada).
6. IF la fecha solicitada cae en sábado o domingo, THEN THE Calendar_Lambda SHALL retornar un mensaje indicando que la fecha es un fin de semana y que las reuniones se agendan únicamente de lunes a viernes.
7. THE Calendar_Lambda SHALL utilizar el endpoint FreeBusy de la Google_Calendar_API para consultar la disponibilidad de múltiples calendarios en una sola solicitud.

### Requisito 2: Acción de Creación de Evento (`create_event`)

**Historia de Usuario:** Como usuario de WhatsApp, quiero agendar una reunión de 30 minutos con el equipo, para que quede registrada en los calendarios de Google.

#### Criterios de Aceptación

1. WHEN el Bedrock_Agent invoca la acción `create_event` con una hora de inicio, THE Calendar_Lambda SHALL crear un evento de 30 minutos en Google Calendar usando la Google_Calendar_API.
2. THE Calendar_Lambda SHALL incluir en el evento creado: título del evento, hora de inicio, hora de fin (inicio + 30 minutos), y las direcciones de correo de Team_Calendars como asistentes.
3. WHEN el evento es creado exitosamente, THE Calendar_Lambda SHALL retornar al Bedrock_Agent una confirmación con la fecha, hora de inicio, hora de fin y un enlace al evento de Google Calendar.
4. IF la hora de inicio solicitada cae fuera del Horario_Laboral, THEN THE Calendar_Lambda SHALL rechazar la solicitud y retornar un mensaje indicando que las reuniones se agendan únicamente dentro del Horario_Laboral.
5. IF la hora de inicio solicitada cae en sábado o domingo, THEN THE Calendar_Lambda SHALL rechazar la solicitud y retornar un mensaje indicando que las reuniones se agendan únicamente de lunes a viernes.
6. WHEN la acción `create_event` es invocada, THE Calendar_Lambda SHALL verificar que el slot solicitado sigue disponible antes de crear el evento para evitar conflictos de agenda.
7. IF el slot solicitado ya no está disponible al momento de crear el evento, THEN THE Calendar_Lambda SHALL retornar un mensaje indicando que el horario ya fue ocupado y sugerir consultar disponibilidad nuevamente.

### Requisito 3: Integración con Google Calendar API mediante Service Account

**Historia de Usuario:** Como desarrollador, quiero que la integración con Google Calendar use una cuenta de servicio con delegación de dominio, para que el sistema acceda a los calendarios del equipo de forma segura y sin intervención manual.

#### Criterios de Aceptación

1. THE Calendar_Lambda SHALL autenticarse con la Google_Calendar_API usando las credenciales de la Service_Account almacenadas en el Credentials_Secret de AWS Secrets Manager.
2. THE Calendar_Lambda SHALL usar delegación a nivel de dominio (domain-wide delegation) de Google Workspace para impersonar a un usuario del dominio al realizar llamadas a la Google_Calendar_API.
3. THE Calendar_Lambda SHALL cachear las credenciales de la Service_Account en memoria durante la vida de la instancia Lambda para evitar llamadas repetidas a Secrets Manager en invocaciones warm-start.
4. IF la lectura de credenciales desde Secrets Manager falla, THEN THE Calendar_Lambda SHALL registrar un log de error y retornar un mensaje de error al Bedrock_Agent indicando que el servicio de calendario no está disponible temporalmente.
5. IF la autenticación con la Google_Calendar_API falla, THEN THE Calendar_Lambda SHALL registrar un log de error con el código de error de Google (sin exponer credenciales en los logs) y retornar un mensaje de error al Bedrock_Agent.

### Requisito 4: Configuración del Action Group en Bedrock Agent

**Historia de Usuario:** Como desarrollador, quiero que el Action Group esté correctamente configurado en el Bedrock Agent existente, para que el agente pueda invocar las acciones de calendario cuando el usuario lo solicite.

#### Criterios de Aceptación

1. THE CDK_Stack SHALL crear un Calendar_Action_Group asociado al Bedrock_Agent existente con las acciones `check_availability` y `create_event`.
2. THE Calendar_Action_Group SHALL definir un esquema de API (OpenAPI) que describa los parámetros de entrada y respuesta de cada acción.
3. WHEN la acción `check_availability` es definida en el esquema, THE Calendar_Action_Group SHALL requerir el parámetro `date` (fecha en formato YYYY-MM-DD) como entrada obligatoria.
4. WHEN la acción `create_event` es definida en el esquema, THE Calendar_Action_Group SHALL requerir los parámetros `start_time` (hora de inicio en formato ISO 8601) y `title` (título del evento) como entradas obligatorias.
5. THE CDK_Stack SHALL configurar la Calendar_Lambda como ejecutor del Calendar_Action_Group.

### Requisito 5: Infraestructura CDK — Calendar Lambda y Permisos

**Historia de Usuario:** Como desarrollador, quiero que la Lambda de calendario y sus permisos se desplieguen con CDK, para que la infraestructura sea reproducible y segura.

#### Criterios de Aceptación

1. THE CDK_Stack SHALL crear una Calendar_Lambda con runtime Python 3.12, un timeout de 30 segundos, y 256 MB de memoria.
2. THE CDK_Stack SHALL crear un Credentials_Secret en AWS Secrets Manager para almacenar el JSON de credenciales de la Service_Account.
3. THE CDK_Stack SHALL configurar los permisos IAM de la Calendar_Lambda para leer el Credentials_Secret de Secrets Manager.
4. THE CDK_Stack SHALL configurar las variables de entorno de la Calendar_Lambda: `CREDENTIALS_SECRET_ARN` (ARN del secreto), `TEAM_CALENDARS` (lista de correos separados por coma), `TIMEZONE` (zona horaria, valor predeterminado: `America/Mexico_City`), y `IMPERSONATE_EMAIL` (correo del usuario a impersonar para delegación de dominio).
5. THE CDK_Stack SHALL empaquetar las dependencias de Python de la Calendar_Lambda (incluyendo `google-auth` y `google-api-python-client`) como parte del código desplegado.
6. THE CDK_Stack SHALL configurar el permiso para que el servicio de Bedrock invoque la Calendar_Lambda.

### Requisito 6: Cálculo de Slots Disponibles

**Historia de Usuario:** Como usuario de WhatsApp, quiero ver únicamente los horarios en los que todo el equipo está libre, para que la reunión no tenga conflictos de agenda.

#### Criterios de Aceptación

1. THE Calendar_Lambda SHALL generar slots candidatos de 30 minutos alineados a la hora y media hora (por ejemplo: 9:00, 9:30, 10:00) dentro del Horario_Laboral para la fecha solicitada.
2. WHEN la respuesta FreeBusy de la Google_Calendar_API indica que un miembro del equipo tiene un evento que se superpone con un slot candidato, THE Calendar_Lambda SHALL excluir ese slot de la lista de Slots_Disponibles.
3. THE Calendar_Lambda SHALL considerar un slot como disponible únicamente cuando todos los miembros de Team_Calendars están libres durante los 30 minutos completos del slot.
4. THE Calendar_Lambda SHALL usar la Timezone_Configurada para determinar los límites del Horario_Laboral y para presentar los horarios al usuario.
5. WHEN la fecha solicitada es el día actual, THE Calendar_Lambda SHALL excluir los slots cuya hora de inicio ya haya pasado.

### Requisito 7: Manejo de Errores y Logging del Calendar Lambda

**Historia de Usuario:** Como desarrollador, quiero que la Lambda de calendario registre logs detallados y maneje errores de forma robusta, para que pueda diagnosticar problemas durante la operación.

#### Criterios de Aceptación

1. THE Calendar_Lambda SHALL registrar en CloudWatch Logs cada invocación recibida del Bedrock_Agent, incluyendo el nombre de la acción y los parámetros de entrada (sin incluir credenciales).
2. THE Calendar_Lambda SHALL registrar en CloudWatch Logs cada llamada a la Google_Calendar_API, incluyendo el endpoint invocado y el tiempo de respuesta en milisegundos.
3. IF una llamada a la Google_Calendar_API falla con un error transitorio (código HTTP 429 o 5xx), THEN THE Calendar_Lambda SHALL reintentar la llamada una vez después de una espera de 1 segundo.
4. IF una llamada a la Google_Calendar_API falla con un error permanente (código HTTP 4xx excepto 429), THEN THE Calendar_Lambda SHALL registrar el error y retornar un mensaje descriptivo al Bedrock_Agent sin reintentar.
5. IF una excepción no controlada ocurre durante el procesamiento, THEN THE Calendar_Lambda SHALL registrar el traceback completo en CloudWatch Logs y retornar un mensaje de error genérico al Bedrock_Agent.

### Requisito 8: Formato de Respuesta del Action Group

**Historia de Usuario:** Como usuario de WhatsApp, quiero recibir la información de disponibilidad y confirmación de reuniones en un formato claro y legible, para que pueda tomar decisiones rápidamente.

#### Criterios de Aceptación

1. WHEN la acción `check_availability` retorna Slots_Disponibles, THE Calendar_Lambda SHALL formatear la respuesta como una lista numerada con la hora de inicio y hora de fin de cada slot (por ejemplo: "1. 10:00 - 10:30").
2. WHEN la acción `create_event` confirma la creación de un evento, THE Calendar_Lambda SHALL incluir en la respuesta la fecha, hora de inicio, hora de fin, título del evento y enlace al evento de Google Calendar.
3. THE Calendar_Lambda SHALL retornar las respuestas en formato de texto plano compatible con la presentación en WhatsApp.
4. THE Calendar_Lambda SHALL expresar todas las horas en la Timezone_Configurada con formato de 24 horas (HH:MM).

### Requisito 9: Validación de Parámetros de Entrada

**Historia de Usuario:** Como desarrollador, quiero que la Lambda valide los parámetros recibidos del Action Group, para que el sistema maneje entradas incorrectas de forma predecible.

#### Criterios de Aceptación

1. WHEN la acción `check_availability` recibe un parámetro `date` que no es una fecha válida en formato YYYY-MM-DD, THE Calendar_Lambda SHALL retornar un mensaje de error indicando el formato esperado.
2. WHEN la acción `create_event` recibe un parámetro `start_time` que no es un timestamp válido en formato ISO 8601, THE Calendar_Lambda SHALL retornar un mensaje de error indicando el formato esperado.
3. WHEN la acción `create_event` recibe un parámetro `title` vacío o con más de 200 caracteres, THE Calendar_Lambda SHALL retornar un mensaje de error indicando las restricciones del título.
4. IF la Calendar_Lambda recibe una acción no reconocida del Bedrock_Agent, THEN THE Calendar_Lambda SHALL registrar un log de advertencia y retornar un mensaje indicando que la acción no es soportada.

