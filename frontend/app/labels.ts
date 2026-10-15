// Russian names for spec values; keep in sync with src/crmbuilder/report.py and automation.py.

export const ENTITY_TITLES: Record<string, string> = {
  deal: 'Сделки',
  lead: 'Лиды',
  contact: 'Контакты',
  company: 'Компании'
}

export const FIELD_TYPES: Record<string, string> = {
  string: 'строка',
  integer: 'целое число',
  double: 'число',
  boolean: 'да/нет',
  date: 'дата',
  datetime: 'дата и время',
  enumeration: 'список',
  employee: 'сотрудник',
  crm: 'привязка к CRM',
  file: 'файл',
  url: 'ссылка',
  money: 'деньги',
  address: 'адрес',
  iblock_element: 'элемент инфоблока',
  iblock_section: 'раздел инфоблока',
  other: 'другой тип'
}

export const ROBOT_TITLES: Record<string, string> = {
  task: 'Поставить задачу',
  notify: 'Уведомление сотруднику',
  sms: 'Отправить SMS клиенту',
  email: 'Отправить письмо клиенту',
  activity: 'Запланировать дело',
  change_responsible: 'Сменить ответственного',
  change_stage: 'Сменить стадию',
  update_fields: 'Изменить элемент',
  create_document: 'Создать документ',
  comment: 'Добавить комментарий',
  webhook: 'Исходящий вебхук',
  other: 'Другой робот'
}

export const TRIGGER_TITLES: Record<string, string> = {
  webform: 'Заполнение CRM-формы',
  call: 'Входящий звонок',
  missed_call: 'Пропущенный звонок',
  email: 'Входящее письмо',
  openline: 'Сообщение в открытую линию',
  payment: 'Оплата',
  field_changed: 'Изменение полей',
  webhook: 'Входящий вебхук',
  other: 'Другой триггер'
}

export const ACTION_TITLES: Record<string, string> = {
  create: 'создать',
  update: 'изменить',
  delete: 'удалить',
  replace: 'пересоздать'
}
